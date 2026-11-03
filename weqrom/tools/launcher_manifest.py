"""Declare the small Weqrom entry/settings activities in binary Android XML.

The original application, TikTok activities and intent filters are retained.
Launcher aliases keep their names, labels, icons and enabled states; only their
targetActivity changes. This module does not inspect or alter executable code.
"""
from __future__ import annotations

import struct

try:
    from .axml import ANDROID, AXMLError, Manifest, NO_INDEX, TYPE_STRING
except ImportError:
    from axml import ANDROID, AXMLError, Manifest, NO_INDEX, TYPE_STRING


LAUNCHER_ACTIVITY = 'app.weqrom.WeqromLauncherActivity'
SETTINGS_ACTIVITY = 'app.weqrom.WeqromSettingsActivity'
INITIALIZER_PROVIDER = 'app.weqrom.WeqromInitializer'
# Android SDK 35 android.R.style.Theme_Material_Light_NoActionBar; API 21+.
THEME_MATERIAL_LIGHT_NO_ACTION_BAR = 0x01030241
# authorities and initOrder IDs verified by compiling against Android SDK35.
_ATTR_IDS = {'theme': 0x01010000, 'name': 0x01010003, 'exported': 0x01010010,
             'authorities': 0x01010018, 'initOrder': 0x0101001A}


class LauncherManifestError(AXMLError):
    """The manifest does not have the expected unmodified launcher structure."""


def _attribute_name(document: Manifest, name: str) -> int:
    """Reuse only an actual framework attribute, not an equal string value."""
    resource_id = _ATTR_IDS[name]
    for index, text in enumerate(document.pool.strings):
        if text == name and index < len(document.resource_ids) and document.resource_ids[index] == resource_id:
            return index
    index = document.pool.intern(name, force_new=True)
    document.resource_ids.extend([0] * (index + 1 - len(document.resource_ids)))
    document.resource_ids[index] = resource_id
    return index


def _activity_chunks(document: Manifest, class_name: str) -> bytes:
    """Encode one empty activity with resource-ID-sorted Android attributes."""
    pool = document.pool
    namespace = pool.intern(ANDROID)
    element = pool.intern('activity')
    name_value = pool.intern(class_name)
    attributes = []
    for name, kind, value, raw in (
        ('theme', 0x01, THEME_MATERIAL_LIGHT_NO_ACTION_BAR, NO_INDEX),
        ('name', TYPE_STRING, name_value, name_value),
        ('exported', 0x12, 0xFFFFFFFF, NO_INDEX),
    ):
        attributes.append(struct.pack('<IIIHBBI', namespace, _attribute_name(document, name), raw, 8, 0, kind, value))
    start_size = 36 + sum(map(len, attributes))
    start = struct.pack('<HHIII', 0x0102, 16, start_size, 0, NO_INDEX)
    start += struct.pack('<II6H', NO_INDEX, element, 20, 20, len(attributes), 0, 0, 0)
    end = struct.pack('<HHIII2I', 0x0103, 16, 24, 0, NO_INDEX, NO_INDEX, element)
    return start + b''.join(attributes) + end


def _class_name(value: str, package: str) -> str:
    if value.startswith('.'):
        return package + value
    return package + '.' + value if '.' not in value else value


def _initializer_chunks(document: Manifest, package: str) -> bytes:
    """A normal private provider runs after Application binding on cold restore."""
    pool = document.pool
    namespace = pool.intern(ANDROID)
    element = pool.intern('provider')
    name_value = pool.intern(INITIALIZER_PROVIDER)
    authority = pool.intern(package + '.weqrom.bootstrap')
    attributes = []
    for name, kind, value, raw in (
        ('name', TYPE_STRING, name_value, name_value),
        ('exported', 0x12, 0, NO_INDEX),
        ('authorities', TYPE_STRING, authority, authority),
        ('initOrder', 0x10, 100, NO_INDEX),
    ):
        attributes.append(struct.pack('<IIIHBBI', namespace, _attribute_name(document, name), raw, 8, 0, kind, value))
    start = struct.pack('<HHIII', 0x0102, 16, 36 + sum(map(len, attributes)), 0, NO_INDEX)
    start += struct.pack('<II6H', NO_INDEX, element, 20, 20, len(attributes), 0, 0, 0)
    end = struct.pack('<HHIII2I', 0x0103, 16, 24, 0, NO_INDEX, NO_INDEX, element)
    return start + b''.join(attributes) + end


def add_weqrom_launcher(data: bytes) -> bytes:
    """Add Weqrom activities before aliases and retarget all launcher aliases.

    Input should be the already branded/versioned original manifest. This is a
    one-time transformation: existing Weqrom component names are rejected rather
    than creating duplicate or silently overriding activity declarations.
    """
    document = Manifest(data)
    pool = document.pool
    applications = [node for node in document.nodes if node.depth == 1 and pool.get(node.name) == 'application']
    if len(applications) != 1:
        raise LauncherManifestError('Expected one application element')
    application = applications[0]
    package_attr = document.root.attr(pool, 'package', None)
    if package_attr is None or package_attr.kind != TYPE_STRING:
        raise LauncherManifestError('Expected a literal manifest package')
    package = package_attr.text(pool)
    own_names = {LAUNCHER_ACTIVITY, SETTINGS_ACTIVITY, INITIALIZER_PROVIDER}
    for node in application.children:
        if pool.get(node.name) not in ('activity', 'activity-alias', 'provider'):
            continue
        name = node.attr(pool, 'name')
        if name is not None and name.kind == TYPE_STRING and _class_name(name.text(pool), package) in own_names:
            raise LauncherManifestError('Weqrom activity is already declared: ' + name.text(pool))
        if pool.get(node.name) == 'provider':
            authority = node.attr(pool, 'authorities')
            if authority is not None and authority.kind == TYPE_STRING and package + '.weqrom.bootstrap' in authority.text(pool).split(';'):
                raise LauncherManifestError('Weqrom initializer authority is already declared')
    aliases = [node for node in application.children if pool.get(node.name) == 'activity-alias' and node.launcher]
    if not aliases:
        raise LauncherManifestError('Expected at least one MAIN/LAUNCHER activity alias')
    # Validate every target before mutating the in-memory document.
    targets = [node.attr(pool, 'targetActivity') for node in aliases]
    if any(target is None or target.kind != TYPE_STRING or not target.text(pool) for target in targets):
        raise LauncherManifestError('Every launcher alias needs a literal targetActivity')
    for target in targets:
        target.set_text(pool, LAUNCHER_ACTIVITY)
    additions = (_activity_chunks(document, LAUNCHER_ACTIVITY)
                 + _activity_chunks(document, SETTINGS_ACTIVITY)
                 + _initializer_chunks(document, package))

    # Build pool/resource-map edits first. Reparse so an inserted resource map
    # cannot invalidate saved chunk indices, then splice after application start.
    rebuilt = Manifest(document.build())
    application = next(node for node in rebuilt.nodes if node.depth == 1 and rebuilt.pool.get(node.name) == 'application')
    output = bytearray(rebuilt.header)
    for index, (_kind, chunk) in enumerate(rebuilt.chunks):
        output.extend(chunk)
        if index == application.chunk_index:
            output.extend(additions)
    struct.pack_into('<I', output, 4, len(output))
    result = bytes(output)
    Manifest(result)
    return result
