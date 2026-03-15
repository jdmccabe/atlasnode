from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from meridian_mcp.store import MeridianStore, _active_embedding_signature, _embed_texts


class MeridianStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(os.environ, {"MERIDIAN_EMBEDDING_BACKEND": "hash"}, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo_root = Path(self.tempdir.name)
        self.repo_root.mkdir(parents=True, exist_ok=True)
        self.windows_env_patch = patch("meridian_mcp.store._read_windows_environment_value", return_value=None)
        self.windows_env_patch.start()
        self.addCleanup(self.windows_env_patch.stop)
        self.store = MeridianStore(repo_root=self.repo_root, data_root=self.repo_root / ".meridian")

    def test_store_initializes_seed_documents_and_state(self) -> None:
        state = self.store.state_response()
        self.assertEqual(state["storage_backend"], "sqlite-hybrid-vector")
        self.assertEqual(state["embedding_backend"], "local-hash-v1")
        self.assertEqual(state["mode"], "technical")
        self.assertIn("system/core", self.store.list_document_ids())
        self.assertIn("mode/technical", self.store.list_document_ids())

    def test_legacy_aliases_resolve_to_logical_documents(self) -> None:
        document = self.store.read_document("brain/MASTER_SPEC.md")
        self.assertEqual(document["id"], "system/core")
        self.assertIn("vector-backed database", document["content"])

    def test_memory_is_vector_searchable(self) -> None:
        memory_id = self.store.write_memory(
            "assistant name is winston",
            "The user wants this assistant to use the name Winston.",
            overwrite=True,
        )
        matches = self.store.search_documents("Winston assistant name", {"memory"}, limit=3)
        self.assertEqual(memory_id, "memory/assistant_name_is_winston")
        self.assertGreaterEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], memory_id)

    def test_chunked_search_recalls_relevant_section(self) -> None:
        body = " ".join(
            ["alpha"] * 220
            + ["database", "vector", "semantic", "recall", "chunking", "retrieval"]
            + ["omega"] * 220
        )
        self.store.write_memory("long retrieval note", body, overwrite=True)
        matches = self.store.search_documents("semantic recall chunking", {"memory"}, limit=3)
        self.assertGreaterEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], "memory/long_retrieval_note")
        self.assertIn("semantic", matches[0]["excerpt"].lower())

    def test_build_system_prompt_uses_runtime_and_memory(self) -> None:
        self.store.write_memory(
            "preferred assistant name",
            "Use the name Winston in Meridian-aware sessions.",
            overwrite=True,
        )
        prompt = self.store.build_system_prompt(
            task="Describe the assistant identity",
            include_memory_summary=True,
            memory_limit=3,
        )
        self.assertIn("Meridian runtime prompt", prompt)
        self.assertIn("mode: technical", prompt)
        self.assertIn("Relevant memory", prompt)
        self.assertIn("Winston", prompt)

    def test_dashboard_snapshot_reports_storage_usage_and_categories(self) -> None:
        self.store.write_memory(
            "preferred assistant name",
            "Use the name Winston in Meridian-aware sessions.",
            overwrite=True,
        )
        self.store.search_documents("Winston assistant", {"memory"}, limit=3)
        snapshot = self.store.dashboard_snapshot(days=14)

        self.assertIn("storage", snapshot)
        self.assertIn("usage", snapshot)
        self.assertIn("categories", snapshot)
        self.assertGreater(snapshot["storage"]["db_file_bytes"], 0)
        self.assertGreaterEqual(snapshot["storage"]["memory_count"], 1)
        self.assertTrue(snapshot["usage"]["daily_added_bytes"])
        self.assertTrue(snapshot["usage"]["daily_retrieved_bytes"])
        self.assertTrue(any(category["id"] == "memory" for category in snapshot["categories"]))

    def test_bge_m3_embedding_backend_is_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            signature = _active_embedding_signature()
        self.assertEqual(signature, "bge-m3:BAAI/bge-m3")

    def test_bge_m3_embedding_backend_can_use_local_model_path(self) -> None:
        fake_model = Mock()
        fake_vector_1 = Mock()
        fake_vector_1.tolist.return_value = [0.1, 0.2]
        fake_vector_2 = Mock()
        fake_vector_2.tolist.return_value = [0.3, 0.4]
        fake_model.encode.return_value = [fake_vector_1, fake_vector_2]

        with patch.dict(
            os.environ,
            {
                "MERIDIAN_EMBEDDING_BACKEND": "bge-m3",
                "MERIDIAN_EMBEDDING_MODEL_PATH": str(self.repo_root / "BAAI--bge-m3"),
            },
            clear=False,
        ):
            signature = _active_embedding_signature()
            with patch("meridian_mcp.store._load_bge_m3_model", return_value=fake_model):
                embeddings = _embed_texts(["first", "second"])

        self.assertIn("BAAI--bge-m3", signature)
        self.assertEqual(len(embeddings), 2)
        self.assertAlmostEqual(embeddings[0][0], 0.1, places=6)
        fake_model.encode.assert_called_once()

    def test_openai_embedding_backend_can_be_selected(self) -> None:
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "data": [
                {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                {"index": 1, "embedding": [0.4, 0.5, 0.6]},
            ]
        }

        with patch.dict(
            os.environ,
            {
                "MERIDIAN_EMBEDDING_BACKEND": "openai",
                "OPENAI_API_KEY": "test-key",
                "MERIDIAN_EMBEDDING_MODEL": "text-embedding-3-small",
                "MERIDIAN_EMBEDDING_DIMENSIONS": "256",
            },
            clear=False,
        ):
            signature = _active_embedding_signature()
            with patch("meridian_mcp.store.httpx.post", return_value=fake_response) as mock_post:
                embeddings = _embed_texts(["first", "second"])

        self.assertEqual(signature, "openai:text-embedding-3-small:256")
        self.assertEqual(len(embeddings), 2)
        self.assertAlmostEqual(embeddings[0][0], 0.1, places=6)
        self.assertAlmostEqual(embeddings[0][1], 0.2, places=6)
        self.assertAlmostEqual(embeddings[0][2], 0.3, places=6)
        self.assertAlmostEqual(embeddings[1][0], 0.4, places=6)
        self.assertAlmostEqual(embeddings[1][1], 0.5, places=6)
        self.assertAlmostEqual(embeddings[1][2], 0.6, places=6)
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.kwargs["json"]["dimensions"], 256)

    def test_openai_embedding_backend_can_use_windows_fallback_key(self) -> None:
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "data": [
                {"index": 0, "embedding": [0.1, 0.2, 0.3]},
            ]
        }

        with patch.dict(os.environ, {"MERIDIAN_EMBEDDING_BACKEND": "openai"}, clear=True):
            with patch("meridian_mcp.store._read_windows_environment_value", return_value="fallback-key"):
                signature = _active_embedding_signature()
                with patch("meridian_mcp.store.httpx.post", return_value=fake_response) as mock_post:
                    embeddings = _embed_texts(["first"])

        self.assertEqual(signature, "openai:text-embedding-3-small")
        self.assertEqual(len(embeddings), 1)
        self.assertAlmostEqual(embeddings[0][0], 0.1, places=6)
        self.assertEqual(mock_post.call_args.kwargs["headers"]["Authorization"], "Bearer fallback-key")


if __name__ == "__main__":
    unittest.main()
