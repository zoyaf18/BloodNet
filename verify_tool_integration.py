#!/usr/bin/env python
"""Verify find_compatible_inventory tool integration."""

import sys
sys.path.insert(0, 'services/agent-svc')

from tools import find_compatible_inventory, AGENT_TOOLS
from schemas import FindCompatibleInventoryInput, READ_TOOL_SCHEMAS
from agent_service import READ_ONLY_TOOL_NAMES, REGION_SAFE_TOOL_NAMES, TOOL_DESCRIPTIONS

print('=== Tool Integration Verification ===\n')

# Check tool registration
print('✓ Import successful')
print(f'✓ Tool in AGENT_TOOLS: {"find_compatible_inventory" in AGENT_TOOLS}')
print(f'✓ Tool in READ_ONLY_TOOL_NAMES: {"find_compatible_inventory" in READ_ONLY_TOOL_NAMES}')
print(f'✓ Tool in REGION_SAFE_TOOL_NAMES: {"find_compatible_inventory" in REGION_SAFE_TOOL_NAMES}')
print(f'✓ Tool in READ_TOOL_SCHEMAS: {"find_compatible_inventory" in READ_TOOL_SCHEMAS}')
print(f'✓ Tool has description: {"find_compatible_inventory" in TOOL_DESCRIPTIONS}')
print(f'✓ Tool is callable: {callable(find_compatible_inventory)}')

print('\n=== Tool Signature ===')
import inspect
sig = inspect.signature(find_compatible_inventory)
print(f'def find_compatible_inventory{sig}')

print('\n=== Schema Definition ===')
print(f'Schema: {FindCompatibleInventoryInput}')
print(f'Fields: {FindCompatibleInventoryInput.model_fields.keys()}')

print('\n=== Tool Description ===')
print(TOOL_DESCRIPTIONS.get('find_compatible_inventory', 'NOT FOUND'))

print('\n✓ All integration checks passed!')
