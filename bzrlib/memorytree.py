# Copyright (C) 2006 Canonical Ltd
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

"""MemoryTree object.

See MemoryTree for more details.
"""

from __future__ import absolute_import

import os

from bzrlib import (
    errors,
    mutabletree,
    revision as _mod_revision,
    )
from bzrlib.decorators import needs_read_lock
from bzrlib.inventory import Inventory
from bzrlib.osutils import sha_file
from bzrlib.mutabletree import needs_tree_write_lock
from bzrlib.transport.memory import MemoryTransport


class MemoryTree(mutabletree.MutableInventoryTree):
    """A MemoryTree is a specialisation of MutableTree.

    It maintains nearly no state outside of read_lock and write_lock
    transactions. (it keeps a reference to the branch, and its last-revision
    only).
    """

    def __init__(self, branch, revision_id):
        """Construct a MemoryTree for branch using revision_id."""
        self.branch = branch
        self.bzrdir = branch.bzrdir
        self._branch_revision_id = revision_id
        self._locks = 0
        self._lock_mode = None

    def get_config_stack(self):
        return self.branch.get_config_stack()

    def is_control_filename(self, filename):
        # Memory tree doesn't have any control filenames
        return False

    @needs_tree_write_lock
    def _add(self, files, ids, kinds):
        """See MutableTree._add."""
        for f, file_id, kind in zip(files, ids, kinds):
            if kind is None:
                kind = 'file'
            if file_id is None:
                self._inventory.add_path(f, kind=kind)
            else:
                self._inventory.add_path(f, kind=kind, file_id=file_id)

    def basis_tree(self):
        """See Tree.basis_tree()."""
        return self._basis_tree

    @staticmethod
    def create_on_branch(branch):
        """Create a MemoryTree for branch, using the last-revision of branch."""
        revision_id = _mod_revision.ensure_null(branch.last_revision())
        return MemoryTree(branch, revision_id)

    def _gather_kinds(self, files, kinds):
        """See MutableTree._gather_kinds.

        This implementation does not care about the file kind of
        missing files, so is a no-op.
        """

    def get_file(self, file_id, path=None):
        """See Tree.get_file."""
        if path is None:
            path = self.id2path(file_id)
        return self._file_transport.get(path)

    def get_file_sha1(self, file_id, path=None, stat_value=None):
        """See Tree.get_file_sha1()."""
        if path is None:
            path = self.id2path(file_id)
        stream = self._file_transport.get(path)
        return sha_file(stream)

    def get_root_id(self):
        return self.path2id('')

    def _comparison_data(self, entry, path):
        """See Tree._comparison_data."""
        if entry is None:
            return None, False, None
        return entry.kind, entry.executable, None

    @needs_tree_write_lock
    def rename_one(self, from_rel, to_rel):
        file_id = self.path2id(from_rel)
        to_dir, to_tail = os.path.split(to_rel)
        to_parent_id = self.path2id(to_dir)
        self._file_transport.move(from_rel, to_rel)
        self._inventory.rename(file_id, to_parent_id, to_tail)

    def path_content_summary(self, path):
        """See Tree.path_content_summary."""
        id = self.path2id(path)
        if id is None:
            return 'missing', None, None, None
        kind = self.kind(id)
        if kind == 'file':
            bytes = self._file_transport.get_bytes(path)
            size = len(bytes)
            executable = self._inventory[id].executable
            sha1 = None # no stat cache
            return (kind, size, executable, sha1)
        elif kind == 'directory':
            # memory tree does not support nested trees yet.
            return kind, None, None, None
        elif kind == 'symlink':
            raise NotImplementedError('symlink support')
        else:
            raise NotImplementedError('unknown kind')

    def _file_size(self, entry, stat_value):
        """See Tree._file_size."""
        if entry is None:
            return 0
        return entry.text_size

    @needs_read_lock
    def get_parent_ids(self):
        """See Tree.get_parent_ids.

        This implementation returns the current cached value from
            self._parent_ids.
        """
        return list(self._parent_ids)

    def has_filename(self, filename):
        """See Tree.has_filename()."""
        return self._file_transport.has(filename)

    def is_executable(self, file_id, path=None):
        return self._inventory[file_id].executable

    def kind(self, file_id):
        return self._inventory[file_id].kind

    def mkdir(self, path, file_id=None):
        """See MutableTree.mkdir()."""
        self.add(path, file_id, 'directory')
        if file_id is None:
            file_id = self.path2id(path)
        self._file_transport.mkdir(path)
        return file_id

    @needs_read_lock
    def last_revision(self):
        """See MutableTree.last_revision."""
        return self._branch_revision_id

    def lock_read(self):
        """Lock the memory tree for reading.

        This triggers population of data from the branch for its revision.
        """
        self._locks += 1
        try:
            if self._locks == 1:
                self.branch.lock_read()
                self._lock_mode = "r"
                self._populate_from_branch()
        except:
            self._locks -= 1
            raise

    def lock_tree_write(self):
        """See MutableTree.lock_tree_write()."""
        self._locks += 1
        try:
            if self._locks == 1:
                self.branch.lock_read()
                self._lock_mode = "w"
                self._populate_from_branch()
            elif self._lock_mode == "r":
                raise errors.ReadOnlyError(self)
        except:
            self._locks -= 1
            raise

    def lock_write(self):
        """See MutableTree.lock_write()."""
        self._locks += 1
        try:
            if self._locks == 1:
                self.branch.lock_write()
                self._lock_mode = "w"
                self._populate_from_branch()
            elif self._lock_mode == "r":
                raise errors.ReadOnlyError(self)
        except:
            self._locks -= 1
            raise

    def _populate_from_branch(self):
        """Populate the in-tree state from the branch."""
        self._set_basis()
        if self._branch_revision_id == _mod_revision.NULL_REVISION:
            self._parent_ids = []
        else:
            self._parent_ids = [self._branch_revision_id]
        self._inventory = Inventory(None, self._basis_tree.get_revision_id())
        self._file_transport = MemoryTransport()
        # TODO copy the revision trees content, or do it lazy, or something.
        inventory_entries = self._basis_tree.iter_entries_by_dir()
        for path, entry in inventory_entries:
            self._inventory.add(entry.copy())
            if path == '':
                continue
            if entry.kind == 'directory':
                self._file_transport.mkdir(path)
            elif entry.kind == 'file':
                self._file_transport.put_file(path,
                    self._basis_tree.get_file(entry.file_id))
            else:
                raise NotImplementedError(self._populate_from_branch)

    def put_file_bytes_non_atomic(self, file_id, bytes):
        """See MutableTree.put_file_bytes_non_atomic."""
        self._file_transport.put_bytes(self.id2path(file_id), bytes)

    def unlock(self):
        """Release a lock.

        This frees all cached state when the last lock context for the tree is
        left.
        """
        if self._locks == 1:
            self._basis_tree = None
            self._parent_ids = []
            self._inventory = None
            try:
                self.branch.unlock()
            finally:
                self._locks = 0
                self._lock_mode = None
        else:
            self._locks -= 1

    @needs_tree_write_lock
    def unversion(self, file_ids):
        """Remove the file ids in file_ids from the current versioned set.

        When a file_id is unversioned, all of its children are automatically
        unversioned.

        :param file_ids: The file ids to stop versioning.
        :raises: NoSuchId if any fileid is not currently versioned.
        """
        # XXX: This should be in mutabletree, but the inventory-save action
        # is not relevant to memory tree. Until that is done in unlock by
        # working tree, we cannot share the implementation.
        for file_id in file_ids:
            if self._inventory.has_id(file_id):
                self._inventory.remove_recursive_id(file_id)
            else:
                raise errors.NoSuchId(self, file_id)

    def set_parent_ids(self, revision_ids, allow_leftmost_as_ghost=False):
        """See MutableTree.set_parent_trees()."""
        for revision_id in revision_ids:
            _mod_revision.check_not_reserved_id(revision_id)
        if len(revision_ids) == 0:
            self._parent_ids = []
            self._branch_revision_id = _mod_revision.NULL_REVISION
        else:
            self._parent_ids = revision_ids
            self._branch_revision_id = revision_ids[0]
        self._allow_leftmost_as_ghost = allow_leftmost_as_ghost
        self._set_basis()
    
    def _set_basis(self):
        try:
            self._basis_tree = self.branch.repository.revision_tree(
                self._branch_revision_id)
        except errors.NoSuchRevision:
            if self._allow_leftmost_as_ghost:
                self._basis_tree = self.branch.repository.revision_tree(
                    _mod_revision.NULL_REVISION)
            else:
                raise

    def set_parent_trees(self, parents_list, allow_leftmost_as_ghost=False):
        """See MutableTree.set_parent_trees()."""
        if len(parents_list) == 0:
            self._parent_ids = []
            self._basis_tree = self.branch.repository.revision_tree(
                                   _mod_revision.NULL_REVISION)
        else:
            if parents_list[0][1] is None and not allow_leftmost_as_ghost:
                # a ghost in the left most parent
                raise errors.GhostRevisionUnusableHere(parents_list[0][0])
            self._parent_ids = [parent_id for parent_id, tree in parents_list]
            if parents_list[0][1] is None or parents_list[0][1] == 'null:':
                self._basis_tree = self.branch.repository.revision_tree(
                                       _mod_revision.NULL_REVISION)
            else:
                self._basis_tree = parents_list[0][1]
            self._branch_revision_id = parents_list[0][0]
