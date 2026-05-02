*Sharing the World's Free Knowledge*

# Internet-in-a-Box (IIAB) — `deldesir` fork

> Customized IIAB with automated SSL, security hardening, AI Gateway, CRM, and e-commerce integrations.

## Quick Start — One Command Install

```bash
curl https://raw.githubusercontent.com/deldesir/iiab-factory/master/install.txt | bash
```

This installs the `deldesir/iiab` fork with all customizations. The installer will prompt you to choose a configuration size and edit `local_vars.yml`.

### Optional: Enable SSL (HTTPS)

**Before** running the installer (or before `sudo iiab` resumes), create or edit `/etc/iiab/local_vars.yml`:

```yaml
# SSL — Let's Encrypt certificate automation
ssl_install: True
ssl_enabled: True
iiab_ssl_enabled: True
iiab_ssl_domain: "yourdomain.com"
iiab_ssl_certbot_email: "you@email.com"
```

If you skip this, IIAB installs normally over HTTP. You can enable SSL later with:

```bash
sudo nano /etc/iiab/local_vars.yml   # add the 5 lines above
cd /opt/iiab/iiab && ./runrole ssl && ./runrole nginx
```

### What's different from upstream IIAB?

| Feature | Upstream | This fork |
|---|---|---|
| SSL/HTTPS | Manual | Automated via `ssl` role + certbot |
| TLS version | 1.0-1.3 | 1.2-1.3 only |
| Firewall | Configurable | Secure defaults (`ports_externally_visible: 2`) + `iptables-persistent` |
| PostgreSQL | Binds to `*` | Binds to `localhost` |
| Backend services | Bind to `*` | WuzAPI binds to `127.0.0.1` |
| Admin Console SSL panel | N/A | Domain + email UI in Network tab |
| Custom apps | — | AI Gateway, RapidPro CRM, Bagisto, Organized, Calibre-Web |

---

[Internet-in-a-Box (IIAB)](https://internet-in-a-box.org) is a "learning hotspot" that brings the Internet's crown jewels (Wikipedia in any language, thousands of Khan Academy videos, zoomable OpenStreetMap, electronic books, WordPress journaling, "Toys from Trash" electronics projects, ETC) to those without Internet.

You can build your own tiny, affordable server (an offline digital library) for your school, your medical clinic, your prison, your region and/or your very own family — accessible with any nearby smartphone, tablet or laptop.

Internet-in-a-Box gives you the DIY tools to:
1. Download then drag-and-drop to arrange the [very best of the World's Free Knowledge](https://internet-in-a-box.org/#quality-content).
2. Choose among [30+ powerful educational apps](https://wiki.iiab.io/go/FAQ#What_services_%28IIAB_apps%29_are_suggested_during_installation%3F) for your school or learning/teaching community, optionally with a complete LMS (learning management system).
3. Exchange local/indigenous knowledge with nearby communities, using our [Manage Content](https://github.com/iiab/iiab-admin-console/blob/master/roles/console/files/help/InstContent.rst#manage-content) interface and possible mesh networking.

FYI this [community product](https://en.wikipedia.org/wiki/Internet-in-a-Box) is enabled by professional volunteers working [side-by-side](https://wiki.iiab.io/go/FAQ#What_are_the_best_places_for_community_support%3F) with schools, clinics and libraries around the world.  *Thank you for being a part of our http://OFF.NETWORK grassroots technology [movement](https://meta.wikimedia.org/wiki/Internet-in-a-Box)!*

## Upstream Installation

Install upstream Internet-in-a-Box (IIAB) from: [**download.iiab.io**](https://download.iiab.io/)

Please see [FAQ.IIAB.IO](https://wiki.iiab.io/go/FAQ) which has 50+ questions and answers to help you along the way. Here are 2 ways to install upstream IIAB:

- The upstream [1-line installer](https://download.iiab.io/) (`curl iiab.io/install.txt | bash`)
- [Prefab disk images](https://github.com/iiab/iiab/wiki/Raspberry-Pi-Images-~-Summary#iiab-images-for-raspberry-pi) for Raspberry Pi

See our [Tech Docs Wiki](https://github.com/iiab/iiab/wiki) for more about the underlying nuts and bolts.

After you've installed the software, you should [add content](https://github.com/iiab/iiab/wiki/IIAB-Installation#add-content), which can of course take time when downloading multi-gigabyte Content Packs!

Finally, you can [customize your Internet-in-a-Box home page](https://wiki.iiab.io/go/FAQ#How_do_I_customize_my_Internet-in-a-Box_home_page%3F) (typically http://box or http://box.lan) using our **drag-and-drop** Admin Console (http://box.lan/admin).

## Community

Global community updates and videos are regularly posted to: **[@internet_in_box](https://twitter.com/internet_in_box)**

_Internet-in-a-Box (IIAB) greatly welcomes contributions from educators, librarians and [IT/UX/QA people](https://github.com/iiab/iiab/wiki/Contributors-Guide-(EN)) of all kinds!_

If you would like to volunteer, please [make contact](https://internet-in-a-box.org/contributing.html) after looking over ["How can I help?"](https://wiki.iiab.io/go/FAQ#How_can_I_help%3F) at: [FAQ.IIAB.IO](https://wiki.iiab.io/go/FAQ)

To learn more about our open community architecture for "offline" learning, check out ["What technical documentation exists?"](https://wiki.iiab.io/go/FAQ#What_technical_documentation_exists%3F)
FYI we use [Ansible](https://wiki.iiab.io/go/FAQ#What_is_Ansible_and_what_version_should_I_use%3F) to install, deploy, configure and manage the various software components.

*Thank you for helping us enable offline access to the Internet's free/open knowledge jewels, as well as "Sneakernet-of-Alexandria" distribution of local/indigenous content, when mass media channels do not serve grassroots voices.*

## Versions

Pre-releases of Internet-in-a-Box (IIAB) undergo continuous QA / continuous integration / continuous deployment and are **strongly recommended!**

Install our latest pre-release using the 1-line installer at: [**download.iiab.io**](https://download.iiab.io/)

You can also consider earlier official releases at: [github.com/iiab/iiab/releases](https://github.com/iiab/iiab/releases)

For much older versions, see: [github.com/xsce](https://github.com/xsce), [schoolserver.org](http://schoolserver.org)
