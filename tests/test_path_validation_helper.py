"""R8-M5: shared `is_path_under_any` / `build_library_allowlist` helpers.

Five blueprints/modules used to spell the "path is inside one of
these allowed roots?" check independently. They disagreed on edge
cases (symlinks resolved vs not, the parent itself, trailing
slashes), and a fix had to be applied in five places. This module
locks the contract so every caller now goes through one helper.
"""

from __future__ import annotations

import os

from bpp.utils.path_validation import build_library_allowlist, is_path_under_any


class TestIsPathUnderAny:
    def test_path_inside_allowed_root(self, tmp_path):
        root = str(tmp_path / "library")
        os.makedirs(root)
        sub = os.path.join(root, "sub", "deep.jpg")
        assert is_path_under_any(sub, [root])

    def test_path_equal_to_allowed_root(self, tmp_path):
        """The parent itself counts as 'under' itself — useful for
        export `outdir == library_path` cases."""
        root = str(tmp_path / "library")
        os.makedirs(root)
        assert is_path_under_any(root, [root])

    def test_path_outside_all_allowed(self, tmp_path):
        root = str(tmp_path / "library")
        outside = str(tmp_path / "elsewhere" / "file.jpg")
        os.makedirs(root)
        os.makedirs(os.path.dirname(outside))
        assert not is_path_under_any(outside, [root])

    def test_empty_allowlist_is_fail_closed(self, tmp_path):
        """Empty allowlist → False. Defensive default — a caller
        that forgot to seed the list shouldn't accidentally allow
        anything."""
        assert not is_path_under_any(str(tmp_path / "anything"), [])
        assert not is_path_under_any(str(tmp_path / "anything"), ())

    def test_symlink_escape_resolved(self, tmp_path):
        """A symlink under an allowed root pointing OUT of the
        allowed root must NOT pass — `os.path.realpath` resolves
        the symlink, the resolved target falls outside, the check
        fails."""
        root = str(tmp_path / "library")
        outside = str(tmp_path / "secret")
        os.makedirs(root)
        os.makedirs(outside)
        sneaky = os.path.join(root, "back-door")
        os.symlink(outside, sneaky)

        # `sneaky` is under `root` lexically, but resolves to
        # `outside` which is NOT under `root`.
        assert not is_path_under_any(sneaky, [root])

    def test_handles_none_or_empty_entries(self, tmp_path):
        """Allowlist entries that are empty/falsey are skipped
        (e.g. `ctx.state.get("workdir")` returning None on a fresh
        startup before the library is set)."""
        root = str(tmp_path / "library")
        os.makedirs(root)
        sub = os.path.join(root, "x.jpg")
        # First entry is empty string — must be skipped, not crash
        assert is_path_under_any(sub, ["", root])

    def test_sibling_with_same_prefix_not_matched(self, tmp_path):
        """Lexical-prefix matches are wrong: a sibling directory
        whose name starts with the allowed root's name (e.g.
        `library_old/`) must NOT be allowed by the check.

        This is the bug `path.startswith(allowed + os.sep)` got
        right but `path.startswith(allowed)` got wrong; lock the
        correct semantics here."""
        root = str(tmp_path / "library")
        sibling = str(tmp_path / "library_old" / "x.jpg")
        os.makedirs(root)
        os.makedirs(os.path.dirname(sibling))
        assert not is_path_under_any(sibling, [root])


class TestBuildLibraryAllowlist:
    def test_builds_with_library_only(self, tmp_path):
        lib = str(tmp_path / "lib")
        os.makedirs(lib)
        result = build_library_allowlist(library_path=lib)
        assert len(result) == 1
        assert result[0] == os.path.realpath(lib)

    def test_builds_with_library_workdir_and_home(self, tmp_path):
        lib = str(tmp_path / "lib")
        wd = str(tmp_path / "wd")
        os.makedirs(lib)
        os.makedirs(wd)
        result = build_library_allowlist(library_path=lib, workdir=wd, include_home=True)
        # 3 entries: lib, wd, home
        assert len(result) == 3
        assert result[0] == os.path.realpath(lib)
        assert result[1] == os.path.realpath(wd)

    def test_drops_none_inputs(self, tmp_path):
        """A common call site is `build_library_allowlist(
        library_path=ctx.state.get('library_path'),
        workdir=ctx.state.get('workdir'))`; if either getter
        returns None, the helper must NOT include an empty-string
        entry (which would otherwise resolve to CWD)."""
        result = build_library_allowlist(library_path=None, workdir=None)
        assert result == []

    def test_resolves_symlinks(self, tmp_path):
        """The result is realpath-resolved so the caller doesn't
        need to. A symlinked library that points elsewhere ends
        up with the target in the allowlist, not the link."""
        target = str(tmp_path / "actual_lib")
        link = str(tmp_path / "link_to_lib")
        os.makedirs(target)
        os.symlink(target, link)
        result = build_library_allowlist(library_path=link)
        assert result == [os.path.realpath(target)]
