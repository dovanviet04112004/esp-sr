"""Make each rule of check_comments, check_layers and check_purity fire on a planted violation."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from tools import check_comments, check_layers, check_purity

ESP_SR_PINNED = 'dependencies:\n  espressif/esp-sr: "==2.5.5"\n'


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def component(root: Path, name: str, requires: str = "", manifest: str = "") -> None:
    write(root, f"components/{name}/CMakeLists.txt", f'idf_component_register(SRCS "x.c" REQUIRES {requires})\n')
    if manifest:
        write(root, f"components/{name}/idf_component.yml", manifest)


class LayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def problems(self) -> list[str]:
        graph = check_layers.collect_components(self.root)
        found = check_layers.check_graph(graph) + check_layers.check_manifests(self.root)
        found += [check_layers.Problem(c[0], "4.5.4", "cycle") for c in check_layers.find_cycles(graph)]
        return [p.render() for p in found]

    def test_a_clean_tree_passes(self) -> None:
        component(self.root, "common")
        component(self.root, "dsp_spec", "common")
        component(self.root, "dsp_afe", "common dsp_spec")
        component(self.root, "svc_front", "common dsp_afe drv_audio ai_engine")
        self.assertEqual(self.problems(), [])

    def test_upward_dependency_fails(self) -> None:
        component(self.root, "dsp_afe", "ai_engine")
        self.assertIn("upward dependency on ai_engine", " ".join(self.problems()))

    def test_sideways_dependency_fails(self) -> None:
        component(self.root, "dsp_spec", "lang_vi")
        self.assertIn("sideways dependency on lang_vi", " ".join(self.problems()))

    def test_forbidden_edge_fails_even_downward(self) -> None:
        component(self.root, "svc_dialog", "svc_listen")
        self.assertIn("through a queue", " ".join(self.problems()))

    def test_component_missing_from_the_table_fails(self) -> None:
        component(self.root, "svc_mystery")
        self.assertIn("absent from the layer table", " ".join(self.problems()))

    def test_esp_sr_in_requires_fails(self) -> None:
        component(self.root, "svc_listen", "espressif__esp-sr")
        self.assertIn("ESP-SR is banned", " ".join(self.problems()))

    def test_esp_sr_in_a_manifest_fails(self) -> None:
        component(self.root, "ai_engine", "common", 'dependencies:\n  espressif/esp-sr: "^2.0"\n')
        self.assertIn("pulls ESP-SR", " ".join(self.problems()))

    def test_esp_sr_in_the_compare_app_passes(self) -> None:
        write(self.root, "test_apps/espsr_compare/main/idf_component.yml", ESP_SR_PINNED)
        self.assertEqual(self.problems(), [])

    def test_esp_sr_in_any_other_app_or_main_fails(self) -> None:
        for rel in ("test_apps/parity/main", "main", "components/dsp_afe/test_apps/unit/main"):
            write(self.root, f"{rel}/idf_component.yml", ESP_SR_PINNED)
        found = [p for p in self.problems() if "pulls ESP-SR" in p]
        self.assertEqual(len(found), 3, found)

    def test_only_the_compare_app_at_its_own_path_is_exempt(self) -> None:
        write(self.root, "test_apps/espsr_compare_old/main/idf_component.yml", ESP_SR_PINNED)
        write(self.root, "components/svc_front/test_apps/espsr_compare/main/idf_component.yml", ESP_SR_PINNED)
        found = [p for p in self.problems() if "pulls ESP-SR" in p]
        self.assertEqual(len(found), 2, found)

    def test_the_compare_app_does_not_excuse_a_component_requiring_esp_sr(self) -> None:
        write(self.root, "test_apps/espsr_compare/main/idf_component.yml", ESP_SR_PINNED)
        component(self.root, "svc_front", "common espressif__esp-sr")
        self.assertIn("ESP-SR is banned", " ".join(self.problems()))


class PurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def details(self) -> list[str]:
        return [p.detail for f in check_purity.pure_sources(self.root) for p in check_purity.check_file(f)]

    def test_pure_code_passes(self) -> None:
        write(self.root, "dsp_x/src/a.c", '#include <stdint.h>\n#include "esp_err.h"\nint f(int x) { return x; }\n')
        self.assertEqual(self.details(), [])

    def test_platform_include_fails(self) -> None:
        write(self.root, "dsp_x/src/a.c", '#include "freertos/FreeRTOS.h"\n')
        self.assertIn("platform header", " ".join(self.details()))

    def test_allocation_fails(self) -> None:
        write(self.root, "lang_x/src/a.c", "void *p(void) { return malloc(4); }\n")
        self.assertIn("allocates with malloc", " ".join(self.details()))

    def test_logging_fails(self) -> None:
        write(self.root, "common/include/a.h", 'static inline void f(void) { ESP_LOGI("t", "x"); }\n')
        self.assertIn("logs with ESP_LOGI", " ".join(self.details()))

    def test_test_apps_and_impure_layers_are_exempt(self) -> None:
        write(self.root, "dsp_x/test_apps/unit/main/t.c", '#include "freertos/FreeRTOS.h"\n')
        write(self.root, "drv_audio/src/a.c", '#include "freertos/FreeRTOS.h"\n')
        self.assertEqual(self.details(), [])

    def test_only_the_fft_wrapper_may_name_heap_caps(self) -> None:
        write(self.root, "dsp_spec/src/fft_dl.c", '#include "esp_heap_caps.h"\n')
        write(self.root, "dsp_spec/src/stft.c", '#include "esp_heap_caps.h"\n')
        details = [
            (p.path.name, p.detail) for f in check_purity.pure_sources(self.root) for p in check_purity.check_file(f)
        ]
        self.assertEqual([name for name, _ in details], ["stft.c"])

    def test_the_fft_wrapper_still_may_not_allocate(self) -> None:
        write(self.root, "dsp_spec/src/fft_dl.c", "void *p(void) { return malloc(4); }\n")
        self.assertIn("allocates with malloc", " ".join(self.details()))

    def test_a_mention_inside_a_comment_is_ignored(self) -> None:
        write(self.root, "dsp_x/src/a.c", "int g(void); // never malloc( here\n")
        self.assertEqual(self.details(), [])


class CommentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def rules(self, rel: str, text: str) -> list[str]:
        return [f"{p.rule} {p.detail}" for p in check_comments.check_file(write(self.root, rel, text))]

    def test_process_word_fails(self) -> None:
        self.assertIn("process comment", " ".join(self.rules("a.c", "// Fixed the gain here.\nint x;\n")))

    def test_box_drawing_banner_fails(self) -> None:
        self.assertIn("banner", " ".join(self.rules("a.py", "# ── grid ──────\nx = 1\n")))

    def test_three_consecutive_body_comments_fail(self) -> None:
        self.assertIn("3 consecutive", " ".join(self.rules("a.c", "// a\n// b\n// c\nint x;\n")))

    def test_doc_comment_in_a_body_file_fails(self) -> None:
        self.assertIn("doc comment in a body file", " ".join(self.rules("a.c", "/** Doc. */\nint x;\n")))

    def test_long_trailing_comment_fails(self) -> None:
        text = "int x; // " + "y" * 60 + "\n"
        self.assertIn("trailing comment", " ".join(self.rules("a.c", text)))

    def test_seven_line_header_doc_fails(self) -> None:
        doc = "/** One.\n * 2\n * 3\n * 4\n * 5\n * 6\n */\nint f(void);\n"
        self.assertIn("doc comment is 7 lines", " ".join(self.rules("a.h", doc)))

    def test_hash_inside_a_python_string_is_not_a_comment(self) -> None:
        text = 'CODE = """\n#include <a.h>\n#include <b.h>\n#include <c.h>\n"""\n'
        self.assertEqual(self.rules("a.py", text), [])

    def test_long_python_trailing_comment_fails(self) -> None:
        text = "x = 1  # " + "y" * 60 + "\n"
        self.assertIn("trailing comment", " ".join(self.rules("a.py", text)))

    def test_prose_starting_with_a_keyword_is_not_code(self) -> None:
        text = "uint32_t handle; // for sys_storage_unmap_models\nuint32_t off; // from the partition start\n"
        self.assertEqual(self.rules("a.h", text), [])

    def test_commented_out_c_and_python_code_fails(self) -> None:
        self.assertIn("commented-out code", " ".join(self.rules("a.c", "int x;\n// for (int i = 0; i < n; i++)\n")))
        self.assertIn("commented-out code", " ".join(self.rules("a.py", "x = 1\n# from os import path\n")))

    def test_generated_file_needs_the_full_banner(self) -> None:
        text = "# GENERATED FILE - DO NOT EDIT.\n# Source: x\nX = 1\n"
        self.assertIn("third banner line", " ".join(self.rules("g.py", text)))


if __name__ == "__main__":
    unittest.main()
