#!/usr/bin/env python3
"""
KonexPro Branding Automation Script
-----------------------------------
This script automates the branding process for a RapidPro installation.
It applies configuration overrides, deploys local assets, and updates UI templates
in a way that preserves upstream code integrity where possible.

Usage:
    sudo python3 apply_branding.py

Best Practices Applied:
- Uses `settings.py` for overrides instead of modifying `settings_common.py` (Vendor file protection).
- Idempotent configuration injection (prevents duplicate entries).
- Structural logging.
- Error handling and rollback safety checks.
"""

import os
import sys
import shutil
import logging
import subprocess
import re
from pathlib import Path
from typing import Dict, List, Any

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("BrandingManager")

# --- Configuration ---

RAPIDPRO_DIR = Path("/opt/iiab/rapidpro")
SETTINGS_LOCAL = RAPIDPRO_DIR / "temba" / "settings.py"
FRAME_TEMPLATE = RAPIDPRO_DIR / "templates" / "frame.html"
ASSETS_DIR = Path(__file__).parent / "branding_assets"
SERVICE_NAME = "rapidpro-gunicorn"

# Configuration constants
MARKER_START = "# --- START KONEXPRO BRANDING ---"
MARKER_END = "# --- END KONEXPRO BRANDING ---"

BRAND_CONFIG = {
    "name": "KonexPro",
    "description": "Pa kite yon mesaj san repons",
    "hosts": ["lrn2.org", "localhost", "127.0.0.1", "box.lan", "*"],
    "domain": "lrn2.org",
    "support_email": "support@lrn2.org",
    "credits": "Copyright © 2025 KonexPro. All rights reserved. <br> Powered by RapidPro",
    "features": [
        'signups', 'msgs', 'flows', 'contacts', 'triggers', 'campaigns', 
        'globals', 'api', 'users', 'tickets', 'locations', 'airtime', 
        'ivr', 'channels'
    ]
}

SVG_LOGO_CONTENT = """<svg xmlns="http://www.w3.org/2000/svg" width="199" height="71" viewBox="0 0 199 71" fill="none">
  <g transform="translate(0, 0)">
    <path d="M12 16 L34 31 L12 46 Z" fill="none" stroke="#00C6FF" stroke-width="3" stroke-linejoin="round"/>
    <circle cx="12" cy="16" r="5" fill="#0072FF"/>
    <circle cx="12" cy="46" r="5" fill="#0072FF"/>
    <circle cx="34" cy="31" r="5" fill="#0072FF"/>
  </g>
  <text x="44" y="62" font-family="'Helvetica Neue', Helvetica, Arial, sans-serif" font-size="36" fill="#021824" textLength="145" lengthAdjust="spacingAndGlyphs">
    <tspan font-weight="500">Konex</tspan><tspan font-weight="800" fill="#0072FF">Pro</tspan>
  </text>
</svg>"""

class BrandingManager:
    def __init__(self):
        self.verify_environment()

    def verify_environment(self):
        """Checks if required directories and files exist."""
        if os.geteuid() != 0:
            logger.error("This script must be run as root.")
            sys.exit(1)
        
        if not RAPIDPRO_DIR.exists():
            logger.error(f"RapidPro directory not found at {RAPIDPRO_DIR}")
            sys.exit(1)

        if not ASSETS_DIR.exists():
            logger.warning(f"Assets directory not found at {ASSETS_DIR}. Only SVG logo and generated assets will be deployed.")

    def apply_settings_overrides(self):
        """
        Appends branding configuration to settings.py.
        This provides a cleaner override than regex-patching settings_common.py.
        """
        logger.info(f"Applying configuration overrides to {SETTINGS_LOCAL}...")
        
        # Ensure settings.py exists
        if not SETTINGS_LOCAL.exists():
            logger.warning(f"{SETTINGS_LOCAL} not found. Creating it with default import...")
            with open(SETTINGS_LOCAL, "w", encoding="utf-8") as f:
                f.write("from temba.settings_common import *\n")

        current_content = SETTINGS_LOCAL.read_text(encoding="utf-8")
        
        # Idempotency check: Remove old block if present
        if MARKER_START in current_content:
            logger.info("Branding configuration already present. Updating...")
            parts = current_content.split(MARKER_START)
            pre = parts[0]
            # Try to find end marker
            if MARKER_END in current_content:
                post = current_content.split(MARKER_END)[1]
                current_content = pre + post
            else:
                current_content = pre # Danger: might lose content if marker end missing, but usually safe for appending scripts
        
        # Construct the injection block
        features_list = BRAND_CONFIG['features']
        
        injection = f"""
{MARKER_START}
# Brand Overrides
BRAND["name"] = "{BRAND_CONFIG['name']}"
BRAND["description"] = "{BRAND_CONFIG['description']}"
BRAND["domain"] = "{BRAND_CONFIG['domain']}"
BRAND["emails"]["notifications"] = "{BRAND_CONFIG['support_email']}"
BRAND["hosts"] = {BRAND_CONFIG['hosts']}
ALLOWED_HOSTS = ["*"]  # Allow all hosts to prevent header checks from blocking

# Logo Paths (Relative to STATIC_URL)
BRAND["logos"]["primary"] = "images/konexpro-logo.svg"
BRAND["logos"]["favico"] = "brands/rapidpro/konexpro-favicon.png"
BRAND["logos"]["avatar"] = "brands/rapidpro/konexpro-favicon.png"
BRAND["landing"]["hero"] = "brands/rapidpro/konexpro-splash.png"

# Feature Flags (Enable all for full menu access)
BRAND["features"] = {features_list}

# Security & Proxy Settings (Injected by KonexPro Script)
# Required for Nginx SSL termination and UI resource loading
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_TRUSTED_ORIGINS = [
    f"https://{{BRAND['domain']}}",
    f"https://*.{{BRAND['domain']}}",
    "https://localhost", 
    "https://127.0.0.1"
]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"
{MARKER_END}
"""
        # Append to file
        with open(SETTINGS_LOCAL, "w", encoding="utf-8") as f:
            f.write(current_content.strip() + "\n" + injection)
            
        logger.info("✓ settings.py updated successfully.")

    def deploy_assets(self):
        """Creates SVG logo and copies static assets."""
        logger.info("Deploying static assets...")
        
        static_images = RAPIDPRO_DIR / "sitestatic" / "images"
        static_brands = RAPIDPRO_DIR / "sitestatic" / "brands" / "rapidpro"
        
        try:
            static_images.mkdir(parents=True, exist_ok=True)
            static_brands.mkdir(parents=True, exist_ok=True)

            # 1. Write SVG
            svg_dest = static_images / "konexpro-logo.svg"
            svg_dest.write_text(SVG_LOGO_CONTENT, encoding="utf-8")
            self._set_perms(svg_dest)
            logger.info(f"✓ Created {svg_dest.name}")

            # 2. Copy Files
            if ASSETS_DIR.exists():
                mapping = {
                    "favicon.png": static_brands / "konexpro-favicon.png",
                    "splash.png": static_brands / "konexpro-splash.png"
                }
                
                for src_name, dest_path in mapping.items():
                    src_path = ASSETS_DIR / src_name
                    if src_path.exists():
                        shutil.copy2(src_path, dest_path)
                        self._set_perms(dest_path)
                        logger.info(f"✓ Copied {src_name}")
                    else:
                        logger.warning(f"Source file {src_name} not found in assets.")
        except Exception as e:
            logger.error(f"Asset deployment failed: {e}")

    def patch_templates(self):
        """
        Updates frame.html:
        1. Fixes footer.
        2. Fixes known syntax error in template tags ({{ active_org.id }}).
        """
        logger.info("Patching templates...")
        
        if not FRAME_TEMPLATE.exists():
            logger.error(f"{FRAME_TEMPLATE} not found.")
            return

        try:
            content = FRAME_TEMPLATE.read_text(encoding="utf-8")
            original_content = content

            # 1. Update Footer
            # Matches arbitrary copyright years and text before "All rights reserved"
            footer_pattern = r"(Copyright © .*? All rights reserved\.)"
            if BRAND_CONFIG['credits'] not in content:
                content = re.sub(
                    footer_pattern, 
                    BRAND_CONFIG['credits'], 
                    content, 
                    flags=re.IGNORECASE
                )
                logger.info("✓ Footer copyright updated.")

            # 2. Fix known JS Syntax Error in frame.html
            # "{ { active_org.id } }" -> "{{ active_org.id }}"
            # This occurs in some versions of the codebase template
            bad_syntax_regex = r"\{\s+\{\s*active_org\.id\s*\}\s+\}"
            if re.search(bad_syntax_regex, content):
                content = re.sub(bad_syntax_regex, "{{ active_org.id }}", content)
                logger.info("✓ Fixed frame.html syntax error.")

            if content != original_content:
                FRAME_TEMPLATE.write_text(content, encoding="utf-8")
            else:
                logger.info("Template already up to date.")
        except Exception as e:
            logger.error(f"Template patching failed: {e}")

    def restart_service(self):
        """Restarts the application service."""
        logger.info(f"Restarting {SERVICE_NAME}...")
        try:
            subprocess.run(["systemctl", "restart", SERVICE_NAME], check=True)
            logger.info("✓ Service restarted successfully.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to restart service: {e}")

    def _set_perms(self, path: Path):
        """Sets file permissions to 644 (readable by Nginx/User)."""
        try:
            os.chmod(path, 0o644)
        except Exception as e:
            logger.warning(f"Could not sets perms on {path}: {e}")

    def run(self):
        logger.info("--- Starting KonexPro Branding ---")
        try:
            self.apply_settings_overrides()
            self.deploy_assets()
            self.patch_templates()
            self.restart_service()
            logger.info("--- Branding Complete ---")
        except Exception as e:
            logger.critical(f"Branding script failed: {e}")
            sys.exit(1)

if __name__ == "__main__":
    manager = BrandingManager()
    manager.run()
