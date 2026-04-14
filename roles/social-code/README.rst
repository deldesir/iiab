# Social Code

A social skills training suite for [Internet-in-a-Box](https://github.com/iiab/iiab).

10 CLI apps covering conversation, humor, emotional intelligence, negotiation, group dynamics, and attraction — all powered by AI simulation and spaced repetition.

## Installation

Add to your `/etc/iiab/local_vars.yml`:

```yaml
social_code_install: True
social_code_enabled: True

# For offline deployments:
social_code_ai_model: ollama/llama3
```

Then run:
```bash
cd /opt/iiab/iiab
sudo ./runrole social-code
```

## Usage

```bash
st train cards         # Flashcard drills
st chat "coffee shop"  # Live simulation
wit train reframe      # Humor drill
deep dive              # Emotional depth
negotiate sim          # Negotiation sim
conduct sim            # Group dynamics
```
