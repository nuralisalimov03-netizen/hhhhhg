"""Small, strict Android binary-XML editor for APK manifest branding.

Existing string indices, style spans, namespaces and unknown XML chunks are
retained. The editor does not change code, resources, SDK levels or signatures.
Optional package cloning cannot fix package/authority literals inside DEX/native
code; callers must validate that separate compatibility concern themselves.
"""
from __future__ import annotations

import re
import struct

NO_INDEX = 0xFFFFFFFF
ANDROID = 'http://schemas.android.com/apk/res/android'
TYPE_STRING = 3
LABEL_ID = 0x01010001


class AXMLError(ValueError):
    pass


def _u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]


def _u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def _length8(data, offset, end):
    if offset >= end:
        raise AXMLError('Truncated UTF-8 string length')
    first = data[offset]
    if first & 0x80:
        if offset + 1 >= end:
            raise AXMLError('Truncated UTF-8 string length')
        return ((first & 0x7F) << 8) | data[offset + 1], offset + 2
    return first, offset + 1


def _length16(data, offset, end):
    if offset + 2 > end:
        raise AXMLError('Truncated UTF-16 string length')
    first = _u16(data, offset)
    if first & 0x8000:
        if offset + 4 > end:
            raise AXMLError('Truncated UTF-16 string length')
        return ((first & 0x7FFF) << 16) | _u16(data, offset + 2), offset + 4
    return first, offset + 2


def _encode_length8(value):
    if value > 0x7FFF:
        raise AXMLError('UTF-8 string exceeds Android length encoding')
    return bytes([0x80 | (value >> 8), value & 0xFF]) if value > 0x7F else bytes([value])


def _encode_length16(value):
    if value > 0x7FFFFFFF:
        raise AXMLError('UTF-16 string exceeds Android length encoding')
    return struct.pack('<HH', 0x8000 | (value >> 16), value & 0xFFFF) if value > 0x7FFF else struct.pack('<H', value)


class StringPool:
    def __init__(self, chunk):
        self.original = bytes(chunk)
        self.header_size = _u16(chunk, 2)
        if self.header_size < 28 or self.header_size > len(chunk):
            raise AXMLError('Invalid string-pool header')
        count, styles, self.flags, start, style_start = struct.unpack_from('<5I', chunk, 8)
        table_end = self.header_size + 4 * (count + styles)
        if count > 1_000_000 or styles > count or table_end > len(chunk) or start < table_end or start > len(chunk):
            raise AXMLError('Invalid string-pool tables')
        if styles and not style_start:
            raise AXMLError('Missing style data')
        if style_start and not start <= style_start <= len(chunk):
            raise AXMLError('Invalid style-data offset')
        end = style_start or len(chunk)
        self.header_extra = bytes(chunk[28:self.header_size])
        self.style_offsets = bytes(chunk[self.header_size + 4 * count:table_end])
        self.style_count = styles
        self.style_data = bytes(chunk[style_start:]) if style_start else b''
        for offset in struct.unpack('<' + 'I' * styles, self.style_offsets) if styles else []:
            if offset != NO_INDEX and (offset >= len(self.style_data) or offset % 4):
                raise AXMLError('Invalid style span offset')
        self.strings = []
        decoded = {}
        for i in range(count):
            relative = _u32(chunk, self.header_size + 4 * i)
            offset = start + relative
            if offset >= end:
                raise AXMLError('String offset outside string data')
            if offset not in decoded:
                try:
                    if self.flags & 0x100:
                        units, position = _length8(chunk, offset, end)
                        length, position = _length8(chunk, position, end)
                        if position + length + 1 > end or chunk[position + length] != 0:
                            raise AXMLError('Invalid UTF-8 string terminator')
                        value = bytes(chunk[position:position + length]).decode('utf-8')
                        if len(value.encode('utf-16le')) // 2 != units:
                            raise AXMLError('UTF-8 string UTF-16 length mismatch')
                    else:
                        units, position = _length16(chunk, offset, end)
                        length = 2 * units
                        if position + length + 2 > end or bytes(chunk[position + length:position + length + 2]) != b'\0\0':
                            raise AXMLError('Invalid UTF-16 string terminator')
                        value = bytes(chunk[position:position + length]).decode('utf-16le')
                    decoded[offset] = value
                except UnicodeError as error:
                    raise AXMLError('Malformed string-pool Unicode') from error
            self.strings.append(decoded[offset])
        self.dirty = False

    def get(self, index):
        if index == NO_INDEX:
            return None
        if index >= len(self.strings):
            raise AXMLError('String index outside string pool')
        return self.strings[index]

    def intern(self, text, force_new=False):
        if not isinstance(text, str) or '\0' in text:
            raise AXMLError('Invalid XML string')
        if not force_new:
            try:
                return self.strings.index(text)
            except ValueError:
                pass
        self.strings.append(text)
        self.dirty = True
        return len(self.strings) - 1

    def build(self):
        if not self.dirty:
            return self.original
        blob = bytearray()
        offsets, shared = [], {}
        for value in self.strings:
            if value not in shared:
                shared[value] = len(blob)
                encoded16 = value.encode('utf-16le')
                units = len(encoded16) // 2
                if self.flags & 0x100:
                    encoded = value.encode('utf-8')
                    blob.extend(_encode_length8(units) + _encode_length8(len(encoded)) + encoded + b'\0')
                else:
                    blob.extend(_encode_length16(units) + encoded16 + b'\0\0')
            offsets.append(shared[value])
        blob.extend(b'\0' * (-len(blob) % 4))
        start = self.header_size + 4 * (len(offsets) + self.style_count)
        style_start = start + len(blob) if self.style_count else 0
        size = start + len(blob) + len(self.style_data)
        header = struct.pack('<HHI5I', 1, self.header_size, size, len(offsets), self.style_count,
                             self.flags & ~1, start, style_start)
        return header + self.header_extra + struct.pack('<' + 'I' * len(offsets), *offsets) + self.style_offsets + bytes(blob) + self.style_data


class Attribute:
    def __init__(self, data, index):
        if len(data) < 20 or _u16(data, 12) != 8:
            raise AXMLError('Invalid XML attribute')
        self.bytes = bytearray(data)
        self.ns, self.name, self.raw = struct.unpack_from('<III', data)
        self.kind = data[15]
        self.data = _u32(data, 16)
        self.old_index = index

    def text(self, pool):
        return pool.get(self.data) if self.kind == TYPE_STRING else pool.get(self.raw)

    def set_text(self, pool, value):
        self.raw = self.data = pool.intern(value)
        self.kind = TYPE_STRING

    def build(self):
        out = self.bytes.copy()
        struct.pack_into('<III', out, 0, self.ns, self.name, self.raw)
        out[15] = self.kind
        struct.pack_into('<I', out, 16, self.data)
        return bytes(out)


class Node:
    def __init__(self, chunk, index, depth):
        self.original = bytes(chunk)
        self.chunk_index, self.depth = index, depth
        self.header_size = _u16(chunk, 2)
        if self.header_size < 16 or self.header_size + 20 > len(chunk):
            raise AXMLError('Invalid start-element header')
        self.ns, self.name = struct.unpack_from('<II', chunk, self.header_size)
        start, self.attribute_size, count, self.id_index, self.class_index, self.style_index = struct.unpack_from('<6H', chunk, self.header_size + 8)
        self.offset = self.header_size + start
        if start < 20 or self.attribute_size < 20 or self.offset + count * self.attribute_size > len(chunk):
            raise AXMLError('Invalid XML attribute bounds')
        if any(i > count for i in (self.id_index, self.class_index, self.style_index)):
            raise AXMLError('Invalid special attribute index')
        self.attributes = [Attribute(chunk[self.offset + i * self.attribute_size:self.offset + (i + 1) * self.attribute_size], i) for i in range(count)]
        self.tail = bytes(chunk[self.offset + count * self.attribute_size:])
        self.children = []
        self.launcher = False

    def attr(self, pool, name, namespace=ANDROID):
        return next((a for a in self.attributes if pool.get(a.name) == name and pool.get(a.ns) == namespace), None)

    def build(self):
        prefix = bytearray(self.original[:self.offset])
        struct.pack_into('<H', prefix, self.header_size + 12, len(self.attributes))
        for old_index, position in [(self.id_index, 14), (self.class_index, 16), (self.style_index, 18)]:
            if old_index:
                adjusted = next(i + 1 for i, attr in enumerate(self.attributes) if attr.old_index == old_index - 1)
                struct.pack_into('<H', prefix, self.header_size + position, adjusted)
        out = prefix + b''.join(a.build() for a in self.attributes) + self.tail
        struct.pack_into('<I', out, 4, len(out))
        return bytes(out)


class Manifest:
    def __init__(self, data):
        if len(data) < 8 or len(data) > 16 * 1024 * 1024:
            raise AXMLError('Invalid manifest size')
        kind, self.header_size, size = struct.unpack_from('<HHI', data)
        if kind != 3 or self.header_size < 8 or size != len(data) or self.header_size > size:
            raise AXMLError('Not a complete Android binary XML document')
        self.header = data[:self.header_size]
        self.chunks, self.nodes, self.resource_ids = [], [], []
        self.pool = None
        self.pool_index = self.resource_index = None
        offset, stack = self.header_size, []
        while offset < size:
            if offset + 8 > size:
                raise AXMLError('Truncated XML chunk header')
            kind, header, length = struct.unpack_from('<HHI', data, offset)
            if header < 8 or length < header or offset + length > size:
                raise AXMLError('Invalid XML chunk bounds')
            chunk = data[offset:offset + length]
            index = len(self.chunks)
            self.chunks.append((kind, chunk))
            if kind == 1:
                if self.pool is not None:
                    raise AXMLError('Multiple string pools are unsupported')
                self.pool, self.pool_index = StringPool(chunk), index
            elif kind == 0x180:
                if self.resource_index is not None or (length - header) % 4:
                    raise AXMLError('Invalid resource map')
                self.resource_index = index
                self.resource_ids = list(struct.unpack('<' + 'I' * ((length - header) // 4), chunk[header:]))
            elif kind == 0x102:
                if self.pool is None:
                    raise AXMLError('Start element precedes string pool')
                node = Node(chunk, index, len(stack))
                self.pool.get(node.name)
                for attr in node.attributes:
                    self.pool.get(attr.ns); self.pool.get(attr.name); self.pool.get(attr.raw)
                    if attr.kind == TYPE_STRING:
                        self.pool.get(attr.data)
                if stack:
                    stack[-1].children.append(node)
                stack.append(node); self.nodes.append(node)
            elif kind == 0x103:
                if not stack or header + 8 > length:
                    raise AXMLError('Unbalanced end element')
                ns, name = struct.unpack_from('<II', chunk, header)
                if name != stack[-1].name or ns != stack[-1].ns:
                    raise AXMLError('Mismatched end element')
                stack.pop()
            offset += length
        if stack or self.pool is None or not self.nodes:
            raise AXMLError('Incomplete XML document')
        roots = [n for n in self.nodes if n.depth == 0]
        if len(roots) != 1 or self.pool.get(roots[0].name) != 'manifest':
            raise AXMLError('Expected one manifest root')
        self.root = roots[0]
        for node in self.nodes:
            if self.pool.get(node.name) not in ('activity', 'activity-alias'):
                continue
            for intent in node.children:
                if self.pool.get(intent.name) != 'intent-filter':
                    continue
                actions, categories = [], []
                for child in intent.children:
                    value = child.attr(self.pool, 'name')
                    if value:
                        target = actions if self.pool.get(child.name) == 'action' else categories if self.pool.get(child.name) == 'category' else []
                        target.append(value.text(self.pool))
                if 'android.intent.action.MAIN' in actions and any(c in categories for c in ('android.intent.category.LAUNCHER', 'android.intent.category.LEANBACK_LAUNCHER')):
                    node.launcher = True

    def label(self, node, value):
        attr = node.attr(self.pool, 'label')
        if attr is None:
            name = next((i for i, text in enumerate(self.pool.strings) if text == 'label' and i < len(self.resource_ids) and self.resource_ids[i] == LABEL_ID), None)
            if name is None:
                name = self.pool.intern('label', force_new=True)
                self.resource_ids.extend([0] * max(0, name + 1 - len(self.resource_ids)))
                self.resource_ids[name] = LABEL_ID
            namespace = self.pool.intern(ANDROID)
            raw = struct.pack('<IIIHBBI', namespace, name, NO_INDEX, 8, 0, TYPE_STRING, 0) + b'\0' * (node.attribute_size - 20)
            attr = Attribute(raw, -1)
            position = next((i for i, a in enumerate(node.attributes) if a.name < len(self.resource_ids) and self.resource_ids[a.name] > LABEL_ID), len(node.attributes))
            node.attributes.insert(position, attr)
        attr.set_text(self.pool, value)

    def build(self):
        replacements = {node.chunk_index: node.build() for node in self.nodes}
        replacements[self.pool_index] = self.pool.build()
        if self.resource_index is not None:
            original = self.chunks[self.resource_index][1]
            header_size = _u16(original, 2)
            resource = bytearray(original[:header_size]) + struct.pack('<' + 'I' * len(self.resource_ids), *self.resource_ids)
            struct.pack_into('<I', resource, 4, len(resource))
            replacements[self.resource_index] = bytes(resource)
        output = bytearray(self.header)
        for i, (_, chunk) in enumerate(self.chunks):
            output.extend(replacements.get(i, chunk))
            if i == self.pool_index and self.resource_index is None and self.resource_ids:
                output.extend(struct.pack('<HHI', 0x180, 8, 8 + 4 * len(self.resource_ids)) + struct.pack('<' + 'I' * len(self.resource_ids), *self.resource_ids))
        struct.pack_into('<I', output, 4, len(output))
        return bytes(output)


def rebrand_manifest(data: bytes, label: str = 'Weqrom', package_name: str | None = None,
                     *, authorities_map: dict[str, str] | None = None,
                     permission_map: dict[str, str] | None = None) -> bytes:
    """Rename launcher labels, optionally changing manifest-level clone identity.

    Package cloning preserves Java class names. Authorities outside the old
    package prefix require an explicit mapping rather than a guessed rewrite.
    No DEX/native strings, server registration or signature expectations change.
    """
    if not isinstance(label, str) or not label.strip() or '\0' in label or len(label) > 200:
        raise AXMLError('Invalid launcher label')
    manifest = Manifest(data)
    pool = manifest.pool
    applications = [n for n in manifest.nodes if pool.get(n.name) == 'application' and n.depth == 1]
    if len(applications) != 1:
        raise AXMLError('Expected one application element')
    manifest.label(applications[0], label)
    for node in manifest.nodes:
        if node.launcher and node.attr(pool, 'label') is not None:
            manifest.label(node, label)
    if package_name is not None:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+', package_name):
            raise AXMLError('Invalid Android package name')
        package = manifest.root.attr(pool, 'package', None)
        if package is None or package.kind != TYPE_STRING:
            raise AXMLError('Manifest package is not a literal string')
        previous = package.text(pool)
        package.set_text(pool, package_name)
        permissions = dict(permission_map or {})
        authorities = dict(authorities_map or {})
        for node in manifest.nodes:
            if pool.get(node.name) in ('permission', 'permission-tree', 'permission-group'):
                attr = node.attr(pool, 'name')
                if attr and attr.kind == TYPE_STRING and attr.text(pool).startswith(previous + '.'):
                    value = attr.text(pool)
                    permissions.setdefault(value, package_name + value[len(previous):])
        class_attrs = {
            'application': ('name', 'backupAgent', 'manageSpaceActivity', 'appComponentFactory'),
            'activity': ('name', 'parentActivityName'),
            'activity-alias': ('name', 'targetActivity', 'parentActivityName'),
            'service': ('name',), 'receiver': ('name',), 'provider': ('name',),
            'instrumentation': ('name',),
        }
        for node in manifest.nodes:
            tag = pool.get(node.name)
            for name in class_attrs.get(tag, ()):
                attr = node.attr(pool, name)
                if attr and attr.kind == TYPE_STRING:
                    value = attr.text(pool)
                    if value.startswith('.'):
                        attr.set_text(pool, previous + value)
                    elif '.' not in value:
                        attr.set_text(pool, previous + '.' + value)
            for name in ('name', 'permission', 'readPermission', 'writePermission', 'permissionGroup'):
                attr = node.attr(pool, name)
                if attr and attr.kind == TYPE_STRING and attr.text(pool) in permissions:
                    attr.set_text(pool, permissions[attr.text(pool)])
            affinity = node.attr(pool, 'taskAffinity')
            if affinity and affinity.kind == TYPE_STRING:
                value = affinity.text(pool)
                if value == previous or value.startswith(previous + '.'):
                    affinity.set_text(pool, package_name + value[len(previous):])
            if tag == 'instrumentation':
                target = node.attr(pool, 'targetPackage')
                if target and target.text(pool) == previous:
                    target.set_text(pool, package_name)
            if tag == 'provider':
                attr = node.attr(pool, 'authorities')
                if attr and attr.kind == TYPE_STRING:
                    result = []
                    for value in attr.text(pool).split(';'):
                        if value in authorities:
                            result.append(authorities[value])
                        elif value == previous or value.startswith(previous + '.'):
                            result.append(package_name + value[len(previous):])
                        else:
                            raise AXMLError('Non-package provider authority requires an explicit mapping: ' + value)
                    attr.set_text(pool, ';'.join(result))
    result = manifest.build()
    # Parse the result independently before returning bytes for packaging.
    Manifest(result)
    return result


def inspect_manifest(data: bytes) -> dict:
    """Return normalized manifest values without resolving resource references."""
    document = Manifest(data)
    pool = document.pool
    package = document.root.attr(pool, 'package', None)
    nodes = []
    for node in document.nodes:
        attrs = []
        for attr in node.attributes:
            value = pool.get(attr.data) if attr.kind == TYPE_STRING else bool(attr.data) if attr.kind == 0x12 else attr.data
            attrs.append({'name': pool.get(attr.name), 'namespace': pool.get(attr.ns),
                          'raw': pool.get(attr.raw), 'type': attr.kind, 'data': attr.data,
                          'value': value, 'resource_id': document.resource_ids[attr.name] if attr.name < len(document.resource_ids) else 0})
        nodes.append({'name': pool.get(node.name), 'depth': node.depth,
                      'launcher': node.launcher, 'attributes': attrs})
    return {'package': package.text(pool) if package else None, 'nodes': nodes}
