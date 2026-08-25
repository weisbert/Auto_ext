"""The built-in parameter catalog.

``options.yaml`` is the data; :mod:`auto_ext.catalog.spec` is its schema,
loader and self-check. Together they replace the per-template
``*.manifest.yaml`` knob system: one table describing every value a generated
EDA input file contains, who owns it, where it lands and how it is spelled --
not only the seven values that happened to be promoted to knobs.

Start at :func:`~auto_ext.catalog.spec.builtin_catalog`.
"""

from __future__ import annotations

from auto_ext.catalog.spec import (
    BUILTIN_CATALOG_PATH,
    Catalog,
    CatalogError,
    ChoicesSource,
    Confidence,
    Currently,
    LandingSite,
    Layout,
    OptionSpec,
    OptionType,
    Owner,
    Quoting,
    RenderRule,
    RenderTargetSpec,
    Screen,
    SectionDisplay,
    Tier,
    UNMAPPED_ORDER,
    TemplateVarAudit,
    audit_template_vars,
    builtin_catalog,
    choices_for,
    default_templates_root,
    load_catalog,
)

__all__ = [
    "BUILTIN_CATALOG_PATH",
    "Catalog",
    "CatalogError",
    "Confidence",
    "Currently",
    "LandingSite",
    "Layout",
    "OptionSpec",
    "OptionType",
    "Owner",
    "Quoting",
    "RenderRule",
    "RenderTargetSpec",
    "Screen",
    "SectionDisplay",
    "Tier",
    "UNMAPPED_ORDER",
    "TemplateVarAudit",
    "audit_template_vars",
    "builtin_catalog",
    "default_templates_root",
    "load_catalog",
]
