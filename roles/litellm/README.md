# LiteLLM Role

This role installs and configures [LiteLLM](https://github.com/BerriAI/litellm), an OpenAI-compatible proxy server for various LLM providers.

## Requirements

*   Debian/Ubuntu-based system
*   Python 3 (handled by role dependency)

## Role Variables

*   `litellm_install`: Set to `True` to install LiteLLM. Default is `True` (in `defaults/main.yml`).
*   `litellm_enabled`: Set to `True` to enable and start the LiteLLM service. Default is `True`.
*   `litellm_port`: The port LiteLLM listens on. Default is `4000`.
*   `litellm_repo`: Git repository URL. Default is `https://github.com/BerriAI/litellm.git`.
*   `litellm_dir`: Installation directory. Default is `/opt/iiab/litellm`.

### Security Requirements

*   `litellm_master_key`: The master key for accessing the LiteLLM proxy. **You MUST override this in `/etc/iiab/local_vars.yml`**.

**Example `/etc/iiab/local_vars.yml`:**
```yaml
litellm_install: True
litellm_enabled: True
litellm_master_key: "sk-your-secure-key"
```

## Services

*   `litellm`: The LiteLLM Python application managed by Systemd.

## Usage

To install and enable LiteLLM, add the configuration to `local_vars.yml` and run:

```bash
cd /opt/iiab/iiab
ansible -m include_role -a "name=litellm" localhost
```
