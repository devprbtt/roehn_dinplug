# ROEHN DINPLUG

Home Assistant custom integration for ROEHN DINPLUG processors over UDP.

## Installation

### HACS

1. In HACS, open the menu for custom repositories.
2. Add this repository as an `Integration`.
3. Download `ROEHN DINPLUG`.
4. Restart Home Assistant.
5. Go to `Settings -> Devices & Services -> Add Integration`.
6. Search for `ROEHN DINPLUG` and complete the config flow.

### Manual

Copy `custom_components/roehn_wizard` into your Home Assistant `custom_components` directory, restart Home Assistant, and add the integration from the UI.

## Configuration

The config flow asks for:

- `Host`
- `Port`
- `Scan interval`

## Validation

This repository includes GitHub Actions for HACS validation and Home Assistant Hassfest validation.
