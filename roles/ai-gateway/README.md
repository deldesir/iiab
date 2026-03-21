# AI Gateway Role

An IIAB Ansible role for the **AI Gateway** — a stateful conversational AI backend
that sits between RapidPro (messaging) and LiteLLM (inference).

- **Port**: `8085`
- **Source**: `https://github.com/deldesir/gateway.git`
- **API**: OpenAI-compatible (`/ai/v1/chat/completions`)
- **Network**: Integrated Nginx reverse proxy at `/ai/v1/`

## Key Variables (`defaults/main.yml`)

| Variable | Default | Description |
|----------|---------|-------------|
| `ai_gateway_install` | `True` | Install the service |
| `ai_gateway_enabled` | `True` | Start and enable service |
| `ai_gateway_dir` | `/opt/iiab/ai-gateway` | Install directory |
| `ai_gateway_repo` | `https://github.com/deldesir/gateway.git` | Source repo |
| `ai_gateway_repo_version` | `feat/b2b-content` | Branch/tag |
| `ai_gateway_port` | `8085` | Listening port |
| `ai_gateway_user` | `{{ iiab_admin_user }}` | Service user |
| `ai_gateway_openai_api_base` | `http://localhost:4000` | LiteLLM endpoint |
| `ai_gateway_openai_api_key` | `{{ litellm_master_key }}` | LiteLLM API key |
| `ai_gateway_llm_model` | `openai/custom_ai` | Primary LLM model alias |
| `ai_gateway_rapidpro_host` | `http://localhost:8080` | RapidPro URL |
| `ai_gateway_rapidpro_token` | `{{ rapidpro_auth_token }}` | RapidPro API token |
| `ai_gateway_admin_phone` | `` | Optional admin phone number |
| `ai_gateway_postgres_uri` | `` | Optional PostgreSQL URI (SQLite used if empty) |

## Usage

Override in `/etc/iiab/local_vars.yml`:

```yaml
ai_gateway_enabled: True
ai_gateway_llm_model: "openai/custom_ai"
ai_gateway_admin_phone: "+50912345678"
```

Then run:

```bash
cd /opt/iiab/iiab && ./runrole ai-gateway
```

## Logs

```bash
journalctl -u ai-gateway -f
```

## Health Check

```bash
curl http://localhost:8085/health
```
