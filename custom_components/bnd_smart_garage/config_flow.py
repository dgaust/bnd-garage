"""Config flow: pair with a hub using an activation code from the B&D app."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ACTIVATION_CODE,
    CONF_CREDENTIALS,
    CONF_SCAN_INTERVAL,
    CONF_USER_PASSWORD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .protocol import (
    AuthenticationError,
    Credentials,
    GarageError,
    HubUnreachableError,
    pair_new_phone,
    read_hub_id,
)
from .protocol.transport import hub_ssl_context

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_PAIR_FIELDS = {
    vol.Required(CONF_ACTIVATION_CODE): str,
    vol.Required(CONF_USER_PASSWORD): _PASSWORD,
}


class BndSmartGarageConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair, re-pair (reauth), and change host (reconfigure)."""

    VERSION = 1

    async def _pair(self, host: str, user_input: dict[str, Any], errors: dict[str, str]) -> Credentials | None:
        ssl_context = await self.hass.async_add_executor_job(hub_ssl_context)
        try:
            return await pair_new_phone(
                async_get_clientsession(self.hass),
                host,
                user_input[CONF_ACTIVATION_CODE],
                user_input[CONF_USER_PASSWORD],
                ssl_context,
            )
        except AuthenticationError:
            errors["base"] = "invalid_auth"
        except HubUnreachableError:
            errors["base"] = "cannot_connect"
        except GarageError as err:
            _LOGGER.warning("Pairing with %s failed: %s", host, err)
            errors["base"] = "pairing_failed"
        except Exception:
            _LOGGER.exception("Unexpected error pairing with %s", host)
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Host + activation code + user password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            # Check identity before spending the one-shot activation code.
            try:
                hub_id = await read_hub_id(host)
            except HubUnreachableError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(hub_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                if credentials := await self._pair(host, user_input, errors):
                    return self.async_create_entry(
                        title=f"B&D hub {credentials.hub_id}",
                        data={CONF_HOST: host, CONF_CREDENTIALS: credentials.as_dict()},
                    )
        schema = vol.Schema({vol.Required(CONF_HOST): str, **_PAIR_FIELDS})
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Credentials rejected (e.g. the HA user was deleted in the app)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Re-pair with a fresh activation code."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            host = entry.data[CONF_HOST]
            if credentials := await self._pair(host, user_input, errors):
                if credentials.hub_id != entry.unique_id:
                    return self.async_abort(reason="wrong_hub")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_CREDENTIALS: credentials.as_dict()}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(_PAIR_FIELDS),
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The hub moved to a new IP: no re-pair needed, just verify identity."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                hub_id = await read_hub_id(host)
            except HubUnreachableError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(hub_id)
                self._abort_if_unique_id_mismatch(reason="wrong_hub")
                return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> BndOptionsFlow:
        """Polling options."""
        return BndOptionsFlow()


class BndOptionsFlow(OptionsFlowWithReload):
    """Idle polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
