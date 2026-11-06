"""Table references use the same schema-scoped metadata and IO as FlowFrames."""

from datetime import datetime
from unittest.mock import Mock

import pytest

from flowfile_core.schemas.catalog_schema import CatalogTableOut
from flowfile_frame import _metadata
from flowfile_frame.catalog_reference import CatalogReference, SchemaReference


@pytest.fixture
def schema():
    catalog = CatalogReference._from_namespace(_metadata.Namespace(id=1, name="General", parent_id=None))
    return SchemaReference._from_namespace(catalog, _metadata.Namespace(id=2, name="default", parent_id=1))


def row(**kwargs):
    return CatalogTableOut(
        id=10,
        name="sales-data",
        owner_id=1,
        namespace_id=2,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
        **kwargs,
    )


def test_get_existing_table_ref_uses_schema_metadata(schema, monkeypatch):
    tables = Mock(return_value=[row(row_count=12, file_path="/tables/sales")])
    monkeypatch.setattr(_metadata, "tables", tables)
    ref = schema.get_table_ref("sales-data")
    tables.assert_called_once_with(schema.id)
    assert ref.schema is schema
    assert ref.name == "sales-data"
    assert ref.exists()
    assert ref.id == 10
    assert ref.row_count == 12
    assert ref.file_path == "/tables/sales"


def test_missing_table_ref_is_not_created(schema, monkeypatch):
    monkeypatch.setattr(_metadata, "tables", Mock(return_value=[]))
    ref = schema.get_table_ref("new-table")
    assert not ref.exists()
    assert ref.id is None
    assert ref.name == "new-table"
    assert ref.schema is schema


def test_lookup_is_scoped_to_schema(schema, monkeypatch):
    other = SchemaReference._from_namespace(schema.catalog, _metadata.Namespace(id=3, name="other", parent_id=1))
    monkeypatch.setattr(_metadata, "tables", lambda namespace_id: [row()] if namespace_id == 2 else [])
    assert schema.get_table_ref("sales-data").exists()
    assert not other.get_table_ref("sales-data").exists()


def test_read_delegates_to_schema(schema, monkeypatch):
    monkeypatch.setattr(_metadata, "tables", Mock(return_value=[row()]))
    read = Mock()
    monkeypatch.setattr(SchemaReference, "read_table", read)
    ref = schema.get_table_ref("sales-data")
    assert ref.read(delta_version=4) is read.return_value
    read.assert_called_once_with("sales-data", delta_version=4)


def test_write_returns_lazy_flowframe_without_refreshing(schema, monkeypatch):
    tables = Mock(return_value=[])
    monkeypatch.setattr(_metadata, "tables", tables)
    write = Mock()
    monkeypatch.setattr(SchemaReference, "write_table", write)
    ref = schema.get_table_ref("sales-data")
    frame = Mock()
    assert ref.write(frame, write_mode="append", description="sales") is write.return_value
    write.assert_called_once_with(frame, "sales-data", write_mode="append", merge_keys=None, description="sales")
    # FlowFrame writes execute at collect time, not when the graph is built.
    assert not ref.exists()
    tables.assert_called_once()


def test_refresh_returns_new_metadata_and_clears_deleted_table(schema, monkeypatch):
    tables = Mock(side_effect=[[row(row_count=1)], [row(row_count=2)], []])
    monkeypatch.setattr(_metadata, "tables", tables)
    ref = schema.get_table_ref("sales-data")
    updated = ref.refresh()
    assert ref.row_count == 1
    assert updated.row_count == 2
    deleted = updated.refresh()
    assert not deleted.exists()
    assert deleted.row_count is None
    assert deleted.file_path is None


def test_lookup_uses_notebook_transport(schema, monkeypatch):
    local = Mock(side_effect=AssertionError("must not query the notebook's local database"))
    monkeypatch.setattr(_metadata, "local_tables", local)
    calls = []

    def transport(body):
        calls.append(body)
        return {"result": [row(row_count=5).model_dump(mode="json")]}

    with _metadata.installed(42, transport):
        ref = schema.get_table_ref("sales-data")
    assert ref.exists()
    assert ref.row_count == 5
    assert len(calls) == 1
    assert calls[0]["kind"] == "tables"
    assert calls[0]["args"] == {"namespace_id": schema.id}
    local.assert_not_called()


def test_lookup_propagates_metadata_errors(schema, monkeypatch):
    monkeypatch.setattr(_metadata, "tables", Mock(side_effect=PermissionError("denied")))
    with pytest.raises(PermissionError, match="denied"):
        schema.get_table_ref("sales-data")
