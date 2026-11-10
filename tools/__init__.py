"""Tool registry assembly. Only implemented tools are exposed."""

from __future__ import annotations

from tools.base import ToolRegistry
from tools.attachment_tools import register_attachment_tools
from tools.browser_tools import register_browser_tools
from tools.document_tools import register_document_tools
from tools.pdf_tools import register_pdf_tools


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_browser_tools(registry)
    register_attachment_tools(registry)
    register_pdf_tools(registry)
    register_document_tools(registry)
    return registry
