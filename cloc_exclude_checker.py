"""
Pylint checker: enforces 'cloc_exclude': ['**/*'] in Odoo __manifest__.py
for third-party modules.

Skip logic (cloc_exclude NOT required) when either:
  - 'author' is in the manifest-required-authors list  →  it's an in-house module
  - 'maintainer' is in the manifest-required-authors list  →  in-house team maintains it

Require cloc_exclude (as a suggestion) when:
  - neither 'author' nor 'maintainer' matches any value in manifest-required-authors

The list of trusted authors is read directly from the existing
[ODOOLINT] manifest-required-authors setting in .pylintrc — no extra
configuration needed in this plugin.

Messages
--------
C9901  missing-cloc-exclude      Key absent — shown as a convention/suggestion
C9902  wrong-cloc-exclude-value  Key present but value is not ['**/*']
"""

import ast
import os

from astroid import nodes
from pylint.checkers import BaseChecker
from pylint.lint import PyLinter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_str_value(dict_node: nodes.Dict, key: str):
    """Return the plain string value for *key* in a manifest Dict, or None."""
    for k_node, v_node in dict_node.items:
        if isinstance(k_node, nodes.Const) and k_node.value == key:
            if isinstance(v_node, nodes.Const) and isinstance(v_node.value, str):
                return v_node.value
    return None


def _get_node(dict_node: nodes.Dict, key: str):
    """Return the raw value node for *key* in a manifest Dict, or None."""
    for k_node, v_node in dict_node.items:
        if isinstance(k_node, nodes.Const) and k_node.value == key:
            return v_node
    return None


def _is_cloc_exclude_valid(value_node: nodes.NodeNG) -> bool:
    """
    Return True if value_node represents ['**/*'].

    Canonical : ['**/*']   (List with one Const element)
    Lenient   : '**/*'     (bare Const string)
    """
    if isinstance(value_node, nodes.List):
        elts = value_node.elts
        return (
            len(elts) == 1
            and isinstance(elts[0], nodes.Const)
            and elts[0].value == "**/*"
        )
    if isinstance(value_node, nodes.Const):
        return value_node.value == "**/*"
    return False


def _parse_required_authors(raw) -> set:
    """
    Parse manifest-required-authors from pylint config into a set of strings.

    pylint_odoo stores this as either:
      - a comma-separated string: "Author A,Author B"
      - already a list/tuple if pylint parsed it
    Handles both forms defensively.
    """
    if not raw:
        return set()
    if isinstance(raw, (list, tuple)):
        return {a.strip() for a in raw if a.strip()}
    # comma-separated string
    return {a.strip() for a in str(raw).split(",") if a.strip()}


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

class ClocExcludeChecker(BaseChecker):
    """
    Suggests adding 'cloc_exclude': ['**/*'] to third-party Odoo modules.

    A module is considered third-party when neither its 'author' nor its
    'maintainer' field matches any value in the [ODOOLINT]
    manifest-required-authors list from .pylintrc.

    No additional configuration required — reuses the existing
    manifest-required-authors option from pylint_odoo.
    """

    name = "manifest-cloc-exclude"

    msgs = {
        "C9901": (
            "Consider adding 'cloc_exclude': ['**/*'] to module '%s'. "
            "Neither the author (%r) nor the maintainer (%r) is a trusted "
            "author. This will exclude the module from Odoo Cloc billing.",
            "missing-cloc-exclude",
            "Third-party modules whose author and maintainer are not in the "
            "manifest-required-authors list should declare "
            "'cloc_exclude': ['**/*'] in their __manifest__.py to prevent "
            "them from being counted in Odoo Cloc billing.",
        ),
        "C9902": (
            "Module '%s' has 'cloc_exclude' but the value is not ['**/*'] "
            "(got %r). Consider setting it to ['**/*'] to fully exclude "
            "this module from Odoo Cloc billing.",
            "wrong-cloc-exclude-value",
            "The 'cloc_exclude' key should be set to ['**/*'] to exclude "
            "the entire module from Odoo Cloc billing.",
        ),
    }

    # No options — reuses manifest-required-authors from pylint_odoo config
    options = ()

    def open(self) -> None:
        """Read manifest-required-authors from pylint_odoo config."""
        raw = getattr(self.linter.config, "manifest_required_authors", None)
        self._trusted_authors: set = _parse_required_authors(raw)

    def visit_module(self, node: nodes.Module) -> None:
        """Called once per file pylint processes."""
        if not node.file or not node.file.endswith("__manifest__.py"):
            return

        manifest_dict = self._find_manifest_dict(node)
        if manifest_dict is None:
            return  # malformed manifest — other checkers handle this

        author = _get_str_value(manifest_dict, "author") or ""
        maintainer = _get_str_value(manifest_dict, "maintainer") or ""

        # Skip if any trusted author is present in author or maintainer
        if self._is_trusted(author) or self._is_trusted(maintainer):
            return

        dir_name = os.path.basename(os.path.dirname(os.path.abspath(node.file)))
        author_display = author or "not set"
        maintainer_display = maintainer or "not set"

        cloc_node = _get_node(manifest_dict, "cloc_exclude")

        if cloc_node is None:
            self.add_message(
                "missing-cloc-exclude",
                node=manifest_dict,
                args=(dir_name, author_display, maintainer_display),
            )
            return

        if not _is_cloc_exclude_valid(cloc_node):
            try:
                actual = ast.literal_eval(cloc_node.as_string())
            except Exception:
                actual = cloc_node.as_string()
            self.add_message(
                "wrong-cloc-exclude-value",
                node=cloc_node,
                args=(dir_name, actual),
            )

    def _is_trusted(self, value: str) -> bool:
        """
        Return True if *value* contains any trusted author name.

        The author field in Odoo manifests is often a comma-separated string
        e.g. "Odoo Development Services, Some Contributor" so we check
        for membership rather than exact equality.
        """
        if not value or not self._trusted_authors:
            return False
        for trusted in self._trusted_authors:
            if trusted in value:
                return True
        return False

    @staticmethod
    def _find_manifest_dict(module_node: nodes.Module):
        """Return the first top-level Dict node, or None."""
        for child in module_node.body:
            if isinstance(child, nodes.Expr) and isinstance(child.value, nodes.Dict):
                return child.value
            if isinstance(child, nodes.Assign) and isinstance(child.value, nodes.Dict):
                return child.value
            if isinstance(child, nodes.AnnAssign) and isinstance(child.value, nodes.Dict):
                return child.value
        return None


# ---------------------------------------------------------------------------
# pylint plugin entry point
# ---------------------------------------------------------------------------

def register(linter: PyLinter) -> None:
    """Called by pylint to register this plugin."""
    linter.register_checker(ClocExcludeChecker(linter))
