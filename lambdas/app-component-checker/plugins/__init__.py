"""
Application Component Plugins
=============================
Plugin architecture for component-specific health checks.

Each plugin must:
1. Inherit from BasePlugin
2. Implement run_all_checks() method
3. Be registered via @register_plugin decorator

Available plugins:
- smui: Search Management UI health checks
- hybris: (planned) SAP Hybris health checks
- solr: (planned) Apache Solr health checks
- apache: (planned) Apache HTTPD health checks
"""

from .base_plugin import BasePlugin

__all__ = ["BasePlugin"]
