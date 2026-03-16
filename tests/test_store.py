from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from atlasnode_mcp.store import AtlasNodeStore, _active_embedding_signature, _embed_texts


class AtlasNodeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(
            os.environ,
            {"ATLASNODE_EMBEDDING_BACKEND": "hash", "ATLASNODE_EXTRACTION_WORKER": "0"},
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.repo_root = Path(self.tempdir.name)
        self.repo_root.mkdir(parents=True, exist_ok=True)
        self.windows_env_patch = patch("atlasnode_mcp.store._read_windows_environment_value", return_value=None)
        self.windows_env_patch.start()
        self.addCleanup(self.windows_env_patch.stop)
        self.store = AtlasNodeStore(repo_root=self.repo_root, data_root=self.repo_root / ".atlasnode")

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
            "Use the name Winston in AtlasNode-aware sessions.",
            overwrite=True,
        )
        prompt = self.store.build_system_prompt(
            task="What was my preferred assistant identity last time?",
            include_memory_summary=True,
            memory_limit=3,
        )
        self.assertIn("AtlasNode runtime prompt", prompt)
        self.assertIn("mode: technical", prompt)
        self.assertIn("Relevant memory", prompt)
        self.assertIn("Winston", prompt)

    def test_build_system_prompt_keeps_memory_gate_closed_without_task_signal(self) -> None:
        self.store.write_memory(
            "preferred assistant name",
            "Use the name Winston in AtlasNode-aware sessions.",
            overwrite=True,
        )
        prompt = self.store.build_system_prompt(
            task="Explain how AtlasNode works.",
            include_memory_summary=True,
            memory_limit=3,
        )
        self.assertIn("memory_gate: closed", prompt)
        self.assertNotIn("## Relevant memory", prompt)

    def test_dashboard_snapshot_reports_storage_usage_and_categories(self) -> None:
        self.store.write_memory(
            "preferred assistant name",
            "Use the name Winston in AtlasNode-aware sessions.",
            overwrite=True,
        )
        self.store.search_documents("Winston assistant", {"memory"}, limit=3)
        snapshot = self.store.dashboard_snapshot(days=14)

        self.assertIn("storage", snapshot)
        self.assertIn("usage", snapshot)
        self.assertIn("categories", snapshot)
        self.assertGreater(snapshot["storage"]["db_file_bytes"], 0)
        self.assertGreaterEqual(snapshot["storage"]["memory_count"], 1)
        self.assertIn("procedure_count", snapshot["storage"])
        self.assertTrue(snapshot["usage"]["daily_added_bytes"])
        self.assertTrue(snapshot["usage"]["daily_retrieved_bytes"])
        self.assertTrue(any(category["id"] == "memory" for category in snapshot["categories"]))
        self.assertIn("health", snapshot)
        self.assertIn("queue", snapshot["health"])
        self.assertIn("procedural", snapshot["recent_context"])

    def test_remember_fact_stores_semantic_metadata(self) -> None:
        memory_id = self.store.remember_fact(
            "preferred plotting library",
            "User prefers matplotlib for quick charts.",
            category="preference",
        )
        document = self.store.read_document(memory_id)
        self.assertEqual(document["metadata"]["kind"], "semantic")
        self.assertEqual(document["metadata"]["category"], "preference")
        self.assertEqual(document["metadata"]["fact_key"], "preferred_plotting_library")
        self.assertEqual(document["metadata"]["fact_status"], "active")
        self.assertEqual(document["metadata"]["fact_version"], 1)

    def test_remember_fact_normalizes_category_aliases(self) -> None:
        memory_id = self.store.remember_fact(
            "current frontend stack",
            "Project AtlasNode dashboard uses vanilla HTML, CSS, and JavaScript.",
            category="project details",
        )
        document = self.store.read_document(memory_id)
        self.assertEqual(document["metadata"]["category"], "project_detail")

    def test_remember_fact_rejects_unknown_category(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown fact category"):
            self.store.remember_fact(
                "odd category example",
                "This should not be stored.",
                category="totally_custom_bucket",
            )

    def test_remember_fact_overwrite_increments_fact_version(self) -> None:
        memory_id = self.store.remember_fact(
            "preferred plotting library",
            "User prefers matplotlib for quick charts.",
            category="preference",
        )
        updated_id = self.store.remember_fact(
            "preferred plotting library",
            "User now prefers plotly for interactive charts.",
            category="preference",
            overwrite=True,
        )
        self.assertEqual(memory_id, updated_id)

        document = self.store.read_document(memory_id)
        self.assertIn("plotly", document["content"].lower())
        self.assertEqual(document["metadata"]["fact_status"], "active")
        self.assertEqual(document["metadata"]["fact_version"], 2)
        self.assertEqual(document["metadata"]["fact_key"], "preferred_plotting_library")

    def test_remember_fact_overwrite_false_raises_for_existing_fact(self) -> None:
        self.store.remember_fact(
            "preferred plotting library",
            "User prefers matplotlib for quick charts.",
            category="preference",
        )
        with self.assertRaises(FileExistsError):
            self.store.remember_fact(
                "preferred plotting library",
                "User now prefers plotly for interactive charts.",
                category="preference",
                overwrite=False,
            )

    def test_remember_fact_rejects_low_value_chatter(self) -> None:
        with self.assertRaisesRegex(ValueError, "low-value chatter"):
            self.store.remember_fact(
                "casual greeting",
                "Thanks!",
                category="general",
            )

    def test_scoped_memory_search_prefers_matching_scope(self) -> None:
        self.store.remember_fact(
            "current frontend stack",
            "Workspace Alpha uses React and Vite.",
            category="project_detail",
            scope="workspace",
            namespace="alpha",
        )
        self.store.remember_fact(
            "deployment environment",
            "Global deployments default to Azure App Service.",
            category="project_detail",
            scope="global",
        )

        scoped = self.store.search_documents(
            "frontend stack deployment environment",
            {"memory"},
            limit=5,
            scope="workspace",
            namespace="alpha",
        )
        self.assertTrue(any(match["id"] == "memory/current_frontend_stack" for match in scoped))
        self.assertTrue(any(match["id"] == "memory/deployment_environment" for match in scoped))
        self.assertGreaterEqual(scoped[0]["scope_rank"], 1)

        other_scope = self.store.search_documents(
            "frontend stack",
            {"memory"},
            limit=5,
            scope="workspace",
            namespace="beta",
            include_global=False,
        )
        self.assertFalse(any(match["id"] == "memory/current_frontend_stack" for match in other_scope))

    def test_superseded_semantic_facts_are_hidden_from_default_retrieval(self) -> None:
        self.store.write_memory(
            "general dashboard note",
            "The dashboard still needs a smaller usage chart legend.",
            overwrite=True,
        )
        with self.store._connect() as connection:
            self.store._upsert_document(
                connection=connection,
                doc_id="memory/obsolete_plotting_preference",
                doc_type="memory",
                title="obsolete plotting preference",
                content="User used to prefer gnuplot.",
                source="memory",
                metadata={
                    "kind": "semantic",
                    "category": "preference",
                    "fact_key": "obsolete_plotting_preference",
                    "fact_status": "superseded",
                    "fact_version": 1,
                    "superseded_by": "memory/preferred_plotting_library",
                    "superseded_at": "2026-03-15T00:00:00Z",
                },
            )

        matches = self.store.search_documents("gnuplot plotting preference", {"memory"}, limit=5)
        self.assertFalse(any(match["id"] == "memory/obsolete_plotting_preference" for match in matches))

        latest = self.store.latest_memories(10)
        self.assertFalse(any(memory["id"] == "memory/obsolete_plotting_preference" for memory in latest))
        self.assertTrue(any(memory["id"] == "memory/general_dashboard_note" for memory in latest))

        summary = self.store.resume_context(memory_limit=10, episode_limit=3, days=30)
        self.assertFalse(any(memory["id"] == "memory/obsolete_plotting_preference" for memory in summary["semantic"]))

    def test_latest_project_status_prioritizes_project_facts_and_recent_episodes(self) -> None:
        self.store.remember_fact(
            "dashboard frontend stack",
            "The AtlasNode dashboard is built with vanilla HTML, CSS, and JavaScript.",
            category="technical",
            scope="workspace",
            namespace="atlasnode",
        )
        self.store.remember_fact(
            "release checklist",
            "Before packaging AtlasNode, rebuild the Windows distribution and rerun tests.",
            category="workflow",
            scope="workspace",
            namespace="atlasnode",
        )
        self.store.log_episode(
            "Finished the scoped memory refactor and verified the tests.",
            title="Scoped memory refactor",
            tags=["backend", "memory"],
            scope="workspace",
            namespace="atlasnode",
        )

        status = self.store.latest_project_status(
            query="what should I do next on AtlasNode?",
            semantic_limit=3,
            episode_limit=3,
            scope="workspace",
            namespace="atlasnode",
        )
        self.assertTrue(status["project_facts"])
        self.assertTrue(status["recent_episodes"])
        self.assertIn(
            status["project_facts"][0]["metadata"]["category"],
            {"project_detail", "workflow", "technical"},
        )
        self.assertEqual(status["recent_episodes"][0]["metadata"]["namespace"], "atlasnode")
        self.assertIsNotNone(status["recommended_focus"])

    def test_remember_procedure_and_search_procedures(self) -> None:
        procedure_id = self.store.remember_procedure(
            "dashboard release flow",
            "Before packaging AtlasNode, rerun tests and rebuild the Windows distribution.",
            scope="workspace",
            namespace="atlasnode",
        )
        self.assertEqual(procedure_id, "procedure/dashboard_release_flow")

        matches = self.store.search_documents(
            "rebuild windows distribution before packaging",
            {"procedure"},
            limit=3,
            scope="workspace",
            namespace="atlasnode",
        )
        self.assertTrue(matches)
        self.assertEqual(matches[0]["id"], procedure_id)
        self.assertEqual(matches[0]["metadata"]["kind"], "procedural")

    def test_background_extraction_queues_and_processes_memory_candidates(self) -> None:
        job_id = self.store.queue_background_extraction(
            (
                "The user prefers BGE-M3 for local embeddings. "
                "Before packaging AtlasNode, rerun tests and rebuild the Windows distribution. "
                "We finished the memory-scope refactor and verified the test suite."
            ),
            scope="workspace",
            namespace="atlasnode",
        )
        self.assertGreater(job_id, 0)

        processed = self.store.process_pending_extractions(limit=5)
        self.assertTrue(any(item["job_id"] == job_id for item in processed))

        memory_matches = self.store.search_documents(
            "BGE-M3 local embeddings",
            {"memory"},
            limit=5,
            scope="workspace",
            namespace="atlasnode",
        )
        procedure_matches = self.store.search_documents(
            "rerun tests rebuild windows distribution",
            {"procedure"},
            limit=5,
            scope="workspace",
            namespace="atlasnode",
        )
        episode_matches = self.store.search_documents(
            "finished scope refactor verified test suite",
            {"episode"},
            limit=5,
            scope="workspace",
            namespace="atlasnode",
        )

        self.assertTrue(memory_matches)
        self.assertTrue(procedure_matches)
        self.assertTrue(episode_matches)

    def test_retry_failed_extractions_requeues_failed_jobs(self) -> None:
        with self.store._connect() as connection:
            connection.execute(
                """
                INSERT INTO extraction_jobs (source_text, scope, namespace, status, result_json, created_at, processed_at)
                VALUES (?, ?, ?, 'failed', ?, ?, ?)
                """,
                (
                    "Before packaging AtlasNode, rerun tests.",
                    "workspace",
                    "atlasnode",
                    '{"error":"simulated"}',
                    "2026-03-15T12:00:00Z",
                    "2026-03-15T12:01:00Z",
                ),
            )

        retried = self.store.retry_failed_extractions(limit=5)
        self.assertEqual(retried, 1)

        snapshot = self.store.dashboard_snapshot(days=14)
        self.assertGreaterEqual(snapshot["health"]["extractions_retried"], 1)
        self.assertGreaterEqual(snapshot["health"]["queue"]["queued"], 1)

    def test_build_system_prompt_includes_relevant_procedures(self) -> None:
        self.store.remember_procedure(
            "dashboard verification flow",
            "Before modifying the AtlasNode dashboard, capture a screenshot and rerun the UI tests.",
        )
        prompt = self.store.build_system_prompt(
            task="Update the AtlasNode dashboard layout safely.",
            include_memory_summary=True,
            memory_limit=4,
        )
        self.assertIn("protocol/procedures", prompt)
        self.assertIn("Relevant procedures", prompt)

    def test_log_episode_and_resume_context_return_recent_history(self) -> None:
        episode_id = self.store.log_episode(
            "Finished the dashboard category-map refactor and verified the layout.",
            title="Dashboard refactor",
            tags=["dashboard", "ui"],
        )
        self.assertTrue(episode_id.startswith("episode/"))

        recent = self.store.recent_episodes(limit=5, days=30)
        self.assertGreaterEqual(len(recent), 1)
        self.assertEqual(recent[0]["id"], episode_id)

        summary = self.store.resume_context(query="continue the dashboard work from last time", memory_limit=3, episode_limit=3, days=30)
        self.assertTrue(summary["episodic"])
        self.assertIsNotNone(summary["gate_reason"])

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
                "ATLASNODE_EMBEDDING_BACKEND": "bge-m3",
                "ATLASNODE_EMBEDDING_MODEL_PATH": str(self.repo_root / "BAAI--bge-m3"),
            },
            clear=False,
        ):
            signature = _active_embedding_signature()
            with patch("atlasnode_mcp.store._load_bge_m3_model", return_value=fake_model):
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
                "ATLASNODE_EMBEDDING_BACKEND": "openai",
                "OPENAI_API_KEY": "test-key",
                "ATLASNODE_EMBEDDING_MODEL": "text-embedding-3-small",
                "ATLASNODE_EMBEDDING_DIMENSIONS": "256",
            },
            clear=False,
        ):
            signature = _active_embedding_signature()
            with patch("atlasnode_mcp.store.httpx.post", return_value=fake_response) as mock_post:
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

        with patch.dict(os.environ, {"ATLASNODE_EMBEDDING_BACKEND": "openai"}, clear=True):
            with patch("atlasnode_mcp.store._read_windows_environment_value", return_value="fallback-key"):
                signature = _active_embedding_signature()
                with patch("atlasnode_mcp.store.httpx.post", return_value=fake_response) as mock_post:
                    embeddings = _embed_texts(["first"])

        self.assertEqual(signature, "openai:text-embedding-3-small")
        self.assertEqual(len(embeddings), 1)
        self.assertAlmostEqual(embeddings[0][0], 0.1, places=6)
        self.assertEqual(mock_post.call_args.kwargs["headers"]["Authorization"], "Bearer fallback-key")


if __name__ == "__main__":
    unittest.main()

