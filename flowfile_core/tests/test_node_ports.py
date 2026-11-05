"""Declarative ports serialize from existing template definitions."""

from pydantic import TypeAdapter

from flowfile_core.schemas.schemas import NodeInput, NodeTemplate


def template(**overrides):
    return NodeTemplate(
        **(
            dict(
                name="Example",
                item="example",
                input=1,
                output=1,
                image="example.svg",
                node_type="process",
                transform_type="narrow",
                node_group="transform",
            )
            | overrides
        )
    )


def test_effective_ports_preserve_union_and_optional_control_contracts():
    union = template(input=10, multi=True)
    assert [(p.id, p.multiple, p.required) for p in union.inputs] == [("input-0", True, True)]
    gate = template(input=2, min_inputs=1, control_input_indices=[1], input_labels=["Data", "Control"])
    assert [(p.kind, p.required) for p in gate.inputs] == [("data", True), ("parameter", False)]


def test_dynamic_instance_serialization_recomputes_without_mutating_palette():
    palette = template(dynamic_inputs=True)
    assert [(p.id, p.hidden) for p in palette.inputs] == [("input-0", True)]
    assert palette.outputs == []
    node = NodeInput(**palette.__dict__, id=7, pos_x=0, pos_y=0, input_names=["", "Orders"])
    node.output_names = ["Result", "Rejected"]
    data = node.model_dump(mode="json")
    assert data["inputs"] == [
        {"id": "input-0", "name": "", "kind": "parameter", "required": False, "multiple": False, "hidden": True},
        {"id": "input-1", "name": "Orders", "kind": "data", "required": False, "multiple": False, "hidden": False},
    ]
    assert [p["id"] for p in data["outputs"]] == ["output-0", "output-1"]
    copied = node.model_copy(update={"input_names": ["Parameters", "Customers"]})
    assert [(p.id, p.kind) for p in copied.inputs] == [("input-0", "parameter"), ("input-1", "data")]
    assert [(p.id, p.hidden) for p in palette.inputs] == [("input-0", True)]
    assert palette.outputs == []


def test_standard_definitions_are_not_mutated_by_port_serialization():
    from copy import deepcopy

    from flowfile_core.configs.node_store.nodes import get_all_standard_nodes

    _, registry, _ = get_all_standard_nodes()
    for node in registry.values():
        before = deepcopy(node.__dict__)
        data = node.model_dump(mode="json")
        assert node.__dict__ == before, node.item
        assert [p["id"] for p in data["inputs"]] == (
            ["input-0"] if node.dynamic_inputs else [f"input-{i}" for i in range(1 if node.multi else node.input)]
        ), node.item
        assert [p["id"] for p in data["outputs"]] == (
            []
            if node.dynamic_inputs
            else [f"output-{i}" for i in range(max(node.output, len(node.output_names or [])))]
        ), node.item
        # Stale or client-supplied descriptors cannot override the original definition.
        restored = NodeTemplate.model_validate(data | {"inputs": [], "outputs": []})
        assert restored.inputs == node.inputs
        assert restored.outputs == node.outputs


def test_palette_serializes_static_ports():
    node = template(input_labels=["Data"])
    data = TypeAdapter(list[NodeTemplate]).dump_python([node], mode="json")[0]
    assert data["inputs"] == [
        {"id": "input-0", "name": "Data", "kind": "data", "required": True, "multiple": False, "hidden": False}
    ]
    assert data["outputs"] == [{"id": "output-0", "name": None}]
