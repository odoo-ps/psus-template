"""
Pylint plugin: checks that __manifest__.py contains 'cloc_exclude': ['**/*']
for a configurable list of module directory names.

Designed for GitHub Actions PR checks — pylint is invoked only on the
__manifest__.py files that appear in the PR diff (passed explicitly on the
command line), so the checker never needs to walk the whole repo.

Configuration (in .pylintrc):
    [MANIFEST-CHECKER]
    cloc-exclude-modules = odoo_module_a, odoo_module_b, my_custom_module

Usage (typically called by the companion script `run_manifest_check.py`):
    pylint --load-plugins=manifest_checker \\
           path/to/odoo_module_a/__manifest__.py \\
           path/to/odoo_module_b/__manifest__.py
"""

import ast
import os
from typing import Set

from astroid import nodes
from pylint.checkers import BaseChecker
from pylint.lint import PyLinter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_module_list(raw: str) -> Set[str]:
    """Parse a comma-separated string into a set of stripped module names."""
    return {name.strip() for name in raw.split(",") if name.strip()}


def _get_dict_key_value(dict_node: nodes.Dict, key: str):
    """Return the value node for *key* in an astroid Dict node, or None."""
    for k_node, v_node in dict_node.items:
        if isinstance(k_node, nodes.Const) and k_node.value == key:
            return v_node
    return None


def _is_cloc_exclude_valid(value_node: nodes.NodeNG) -> bool:
    """
    Return True if *value_node* represents the list ['**/*'].

    Accepts:
        - astroid List node containing exactly one element '**/*'
        - astroid Const node with value '**/*'  (lenient string fallback)
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


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

class ManifestClocExcludeChecker(BaseChecker):
    """
    Checks __manifest__.py for a mandatory 'cloc_exclude': ['**/*'] entry.

    Only files whose *parent directory name* appears in the configured
    `cloc-exclude-modules` list are checked.  All other manifests are
    silently skipped.

    In a GitHub Actions PR workflow the caller script resolves which
    __manifest__.py files were touched in the diff and passes only those
    paths to pylint, so this checker naturally operates on the diff only.
    """

    name = "manifest-cloc-exclude"

    msgs = {
        "W9901": (
            "Module '%s' is in the cloc-exclude list but __manifest__.py "
            "is missing 'cloc_exclude': ['**/*']",
            "missing-cloc-exclude",
            "Modules listed in cloc-exclude-modules must declare "
            "'cloc_exclude': ['**/*'] in their __manifest__.py.",
        ),
        "W9902": (
            "Module '%s' is in the cloc-exclude list but 'cloc_exclude' "
            "value is not ['**/*'] (got %r)",
            "wrong-cloc-exclude-value",
            "The 'cloc_exclude' key must be set to ['**/*'].",
        ),
        "W9903": (
            "__manifest__.py does not contain a dict literal at module level",
            "invalid-manifest-structure",
            "The __manifest__.py file must contain exactly one dict literal "
            "assigned or expressed at module level.",
        ),
    }

    options = (
        (
            "cloc-exclude-modules",
            {
                "default": "",
                "type": "string",
                "metavar": "<module1,module2,...>",
                "help": (
                    "Comma-separated list of module directory names whose "
                    "__manifest__.py must contain 'cloc_exclude': ['**/*']. "
                    "Example: odoo_module_a,odoo_module_b,my_custom_module"
                ),
            },
        ),
    )

    def open(self) -> None:
        """Parse the module list once when the checker session opens."""
        raw = self.linter.config.cloc_exclude_modules  # pylint: disable=no-member
        self._required_modules: Set[str] = _parse_module_list(raw)

    # ------------------------------------------------------------------
    # AST visitor — entry point for every file pylint processes
    # ------------------------------------------------------------------

    def visit_module(self, node: nodes.Module) -> None:
        """
        Called once per file.

        Guard rails:
          1. File must be named __manifest__.py
          2. Parent directory name must be in self._required_modules
        Everything else is silently skipped.
        """
        if not node.file or not node.file.endswith("__manifest__.py"):
            return

        manifest_dir = os.path.dirname(os.path.abspath(node.file))
        dir_name = os.path.basename(manifest_dir)

        if not self._required_modules or dir_name not in self._required_modules:
            return

        manifest_dict = self._find_manifest_dict(node)
        if manifest_dict is None:
            self.add_message("invalid-manifest-structure", node=node)
            return

        cloc_value = _get_dict_key_value(manifest_dict, "cloc_exclude")

        if cloc_value is None:
            self.add_message(
                "missing-cloc-exclude",
                node=manifest_dict,
                args=(dir_name,),
            )
            return

        if not _is_cloc_exclude_valid(cloc_value):
            try:
                actual = ast.literal_eval(cloc_value.as_string())
            except Exception:
                actual = cloc_value.as_string()
            self.add_message(
                "wrong-cloc-exclude-value",
                node=cloc_value,
                args=(dir_name, actual),
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_manifest_dict(module_node: nodes.Module):
        """
        Return the first top-level Dict node in the module, or None.

        Handles three patterns:
            {'name': '...', ...}                    ← bare Expr
            manifest = {'name': '...', ...}         ← Assign
            manifest: dict = {'name': '...', ...}   ← AnnAssign
        """
        for child in module_node.body:
            if isinstance(child, nodes.Expr) and isinstance(child.value, nodes.Dict):
                return child.value
            if isinstance(child, nodes.Assign) and isinstance(child.value, nodes.Dict):
                return child.value
            if isinstance(child, nodes.AnnAssign) and isinstance(child.value, nodes.Dict):
                return child.value
        return None


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(linter: PyLinter) -> None:
    """Register the checker with pylint."""
    linter.register_checker(ManifestClocExcludeChecker(linter))