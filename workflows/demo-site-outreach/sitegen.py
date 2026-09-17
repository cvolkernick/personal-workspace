"""Static one-page demo generator. Template version is pinned per lead."""

from __future__ import annotations

import html
import urllib.parse
from pathlib import Path

from models import TEMPLATE_VERSION, Lead

PKG = Path(__file__).resolve().parent
TEMPLATES = PKG / "templates"


def generate_site(lead: Lead) -> dict[str, str]:
    version = lead.template_version or TEMPLATE_VERSION
    tdir = TEMPLATES / version
    index_path = tdir / "index.html"
    css_path = tdir / "styles.css"
    if not index_path.is_file():
        raise FileNotFoundError(f"template {version} missing: {index_path}")
    mapping = _mapping(lead)
    index = index_path.read_text(encoding="utf-8")
    for key, value in mapping.items():
        index = index.replace("{{" + key + "}}", value)
    files = {"index.html": index}
    if css_path.is_file():
        files["styles.css"] = css_path.read_text(encoding="utf-8")
    return files


def _mapping(lead: Lead) -> dict[str, str]:
    d = lead.discovery
    services = d.get("services") or lead.category or "Local services"
    hours = d.get("hours") or lead.hours or "Call for hours"
    contact = d.get("contact") or lead.phone or lead.email or "Contact us"
    differentiator = d.get("differentiator") or "Local, reliable, and easy to reach."
    photos_note = d.get("photos") or ""
    phone = lead.sms_number or lead.phone
    tel = "".join(ch for ch in phone if ch.isdigit())
    if len(tel) == 10:
        tel_href = "+1" + tel
        tel_display = f"({tel[:3]}) {tel[3:6]}-{tel[6:]}"
    else:
        tel_href = tel
        tel_display = phone or "Call us"
    reviews_html = _reviews_html(lead)
    services_html = _services_html(services)
    map_query = urllib.parse.quote(lead.address or lead.name)
    neighborhood = lead.neighborhood or (lead.address.split(",")[0] if lead.address else "")
    initials = "".join(part[0] for part in lead.name.split()[:2] if part).upper() or "•"
    rating = f" · {lead.rating:g}★" if lead.rating else ""
    return {
        "name": html.escape(lead.name),
        "initials": html.escape(initials),
        "category": html.escape(lead.category or "Local business"),
        "neighborhood": html.escape(neighborhood or lead.geo or "United States"),
        "address": html.escape(lead.address),
        "services": html.escape(services),
        "services_html": services_html,
        "hours": html.escape(hours),
        "contact": html.escape(contact),
        "differentiator": html.escape(differentiator),
        "photos_note": html.escape(photos_note),
        "phone_display": html.escape(tel_display),
        "phone_href": html.escape(tel_href),
        "email": html.escape(lead.email),
        "reviews_html": reviews_html,
        "map_query": html.escape(map_query),
        "rating": html.escape(rating),
        "template_version": html.escape(lead.template_version or TEMPLATE_VERSION),
    }


def _services_html(services: str) -> str:
    parts = [p.strip() for p in services.replace(";", ",").split(",") if p.strip()]
    if not parts:
        parts = [services]
    cards = []
    for part in parts[:8]:
        cards.append(f'<article class="card"><h3>{html.escape(part)}</h3></article>')
    return "\n".join(cards)


def _reviews_html(lead: Lead) -> str:
    snippets = [s.strip() for s in (lead.reviews or []) if str(s).strip()][:3]
    if not snippets:
        return '<p class="muted">Reviews will appear here when neighbors weigh in.</p>'
    blocks = []
    for snippet in snippets:
        blocks.append(f"<blockquote>{html.escape(snippet)}</blockquote>")
    return "\n".join(blocks)
