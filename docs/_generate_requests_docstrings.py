"""This file iterates through all of the python files in the smpclient/requests
directory and changes them. It will import the classes and check if they have
a docstring or not. If they do not have a docstring, it will get the docstring
from the parent class, add it to the class, and rewrite the file.

This is solely for generating documentation with mkdocs.

It is wrangled LLM code and should be replaced ASAP.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import os
from typing import Any, List, Optional, Type

from pydantic import BaseModel


class ClassInfo:
    def __init__(self, name: str, lineno: int, col_offset: int, original_text: str):
        self.name = name
        self.lineno = lineno
        self.col_offset = col_offset
        self.original_text = original_text
        self.docstring: Optional[str] = None

    def add_docstring(self, docstring: str) -> None:
        """Add a docstring to the class."""
        indent = ' ' * (self.col_offset + 4)
        formatted_docstring = format_docstring(docstring, indent)
        self.docstring = formatted_docstring

    def get_updated_text(self) -> str:
        """Get the updated class text with the new docstring."""
        if self.docstring:
            lines = self.original_text.split('\n')
            lines.insert(1, self.docstring)
            return '\n'.join(lines)
        return self.original_text


def format_docstring(docstring: str, indent: str) -> str:
    """Format the docstring with the correct indentation."""
    lines = docstring.split('\n')
    indented_lines = [f'{indent}"""{lines[0]}\n']
    indented_lines += [f'{line}' for line in lines[1:]]
    indented_lines.append(f'{indent}"""')
    return '\n'.join(indented_lines)


def get_docstring_from_parent(cls: type) -> Optional[str]:
    """Get the docstring from the parent class."""
    for base in cls.__bases__:
        if base.__doc__:
            return base.__doc__
    return None


def get_field_docstring(cls: Type[BaseModel], field_name: str) -> str:
    """Get the docstring of a field from the class."""
    for name, obj in inspect.getmembers(cls):
        if name == field_name:
            return obj.__doc__ or "No docstring provided."
    return "No docstring found."


def format_type(annotation: Type[Any] | None) -> str:
    """Format the type to show module and class name."""
    if annotation is None:
        raise ValueError("Annotation cannot be None")
    if hasattr(annotation, '__name__'):  # Handles regular types like `int`, `str`, etc.
        # get the annotations like List[str] for example
        if hasattr(annotation, '__args__'):
            return f"{annotation.__name__}[{format_type(annotation.__args__[0])}]"
        return f"{annotation.__name__}"
    elif hasattr(annotation, '__origin__'):  # Handles generic types like List[str], Optional[int]
        return f"{annotation.__origin__.__module__}.{annotation.__origin__.__name__}"
    return str(annotation)  # Fallback for other types


def get_pydantic_fields(cls: Type[BaseModel]) -> str:
    """Get the fields of a Pydantic model and format them as Google-style Args."""
    if not issubclass(cls, BaseModel):
        return ""

    fields = cls.model_fields
    args = "\n    Args:\n"
    for field_name, field_info in fields.items():
        if field_name in ("header, version, sequence, smp_data"):
            continue
        field_type = format_type(field_info.annotation)

        # split the field_info.description by newlines and join them with a newline
        # and 12 spaces, removing blank lines
        description = (
            "\n            ".join(
                filter(lambda x: x.strip() != "", field_info.description.split("\n"))
            )
            if field_info.description
            else ""
        )

        args += f"        {field_name} ({field_type}): {description}\n"
    if args.endswith("Args:\n"):
        return ""
    return args


def parse_file(file_path: str) -> List[ClassInfo]:
    """Parse the file and extract class definitions."""
    with open(file_path, 'r') as file:
        lines = file.readlines()
        tree = ast.parse(''.join(lines))

    classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            lineno = node.lineno - 1
            col_offset = node.col_offset
            class_body = lines[lineno : lineno + len(node.body) + 1]
            original_text = ''.join(class_body)
            classes.append(ClassInfo(class_name, lineno, col_offset, original_text))
    return classes


def update_class_docstrings(file_path: str) -> None:
    """Update class docstrings in a given file."""
    classes = parse_file(file_path)
    module_name = file_path.replace('/', '.').replace('.py', '')
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None:
        raise ValueError(f"Could not find spec for {module_name}")
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ValueError(f"Could not find loader for {module_name}")
    spec.loader.exec_module(module)

    for class_info in classes:
        cls = getattr(module, class_info.name)
        if not cls.__doc__:
            parent_docstring = get_docstring_from_parent(cls)
            if parent_docstring:
                args_section = get_pydantic_fields(cls)
                full_docstring = parent_docstring + args_section
                class_info.add_docstring(full_docstring)

    with open(file_path, 'r', encoding="utf-8") as file:
        lines = file.readlines()

    updated_lines = []
    class_index = 0
    for i, line in enumerate(lines):
        if class_index < len(classes) and i == classes[class_index].lineno:
            updated_lines.append(classes[class_index].get_updated_text())
            class_index += 1
        else:
            updated_lines.append(line)

    with open(file_path, 'w', encoding="utf-8") as file:
        file.writelines(updated_lines)


def main() -> None:
    directory = 'smpclient/requests'
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                update_class_docstrings(file_path)


if __name__ == '__main__':
    main()
