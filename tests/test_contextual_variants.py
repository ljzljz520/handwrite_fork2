import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from handwrite import SHEETtoPNG, PNGtoSVG, SVGtoTTF
from PIL import Image, ImageChops

CONFIG_FILE = os.path.join(
    os.path.dirname(__file__), "test_data", "config_data", "default.json"
)
SHEET = os.path.join(
    os.path.dirname(__file__), "test_data", "sheettopng", "excellent.jpg"
)

CLUSTER_RE = re.compile(r"([\w.]+)=(\d+)(?:@(-?\d+)(?:,(-?\d+))?)?\+(-?\d+)")


def _run_hb_shape(font_path, text, features, glyph_ids):
    command = ["hb-shape"]
    if glyph_ids:
        command.append("--no-glyph-names")
    for feature in features:
        command += ["--features", feature]
    command += [font_path, text]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


def hb_shape(font_path, text, features=()):
    """Shape ``text`` with HarfBuzz' hb-shape CLI.

    With glyph names shown the output is ``name=cluster+advance``, so this
    returns ``(glyph_name, advance)`` tuples. Use :func:`hb_shape_ids` when
    the actual glyph ids are needed.
    """
    output = _run_hb_shape(font_path, text, features, glyph_ids=False)
    return [
        (name, int(advance)) for name, _c, _x, _y, advance in CLUSTER_RE.findall(output)
    ]


def hb_shape_ids(font_path, text, features=()):
    """Shape ``text`` returning ``(glyph_id, advance)`` tuples."""
    output = _run_hb_shape(font_path, text, features, glyph_ids=True)
    return [
        (int(gid), int(advance))
        for gid, _c, _x, _y, advance in CLUSTER_RE.findall(output)
    ]


def raw_width(font_path, text_with_glyph_last):
    """Kerning never applies to the last glyph, so its advance is raw."""
    return hb_shape(font_path, text_with_glyph_last)[-1][1]


def write_variant_png(src, dst, shift, bar_x):
    img = Image.open(src).convert("L")
    w, h = img.size
    out = Image.new("L", (w, h), 255)
    out.paste(ImageChops.offset(img, shift, 0), (0, 0))
    # Extra ink on one side: makes the outline and its advance width differ
    # from the base sample, giving every variant independent metrics.
    for x in range(bar_x, min(bar_x + 6, w)):
        for y in range(h // 4, 3 * h // 4):
            out.putpixel((x, y), 0)
    out.save(dst)


class TestVariantDiscovery(unittest.TestCase):
    """Pure unit tests for sample discovery (no Fontforge required)."""

    def _converter(self, settings=None):
        converter = SVGtoTTF()
        converter.config = {"contextual_variants": settings or {}}
        return converter

    def test_primary_first_then_lexicographic_extras(self):
        directory = tempfile.mkdtemp()
        os.makedirs(os.path.join(directory, "108"))
        for name in ("108.svg", "108.curly.svg", "108.swing.svg", "108.bmp"):
            open(os.path.join(directory, "108", name), "w").close()
        files = self._converter()._discover_variant_files(directory, 108)
        self.assertEqual(
            [os.path.basename(f) for f in files],
            ["108.svg", "108.curly.svg", "108.swing.svg"],
        )
        shutil.rmtree(directory)

    def test_cap_limits_samples(self):
        directory = tempfile.mkdtemp()
        os.makedirs(os.path.join(directory, "108"))
        for name in ("108.svg", "108.2.svg", "108.3.svg", "108.4.svg"):
            open(os.path.join(directory, "108", name), "w").close()
        converter = self._converter({"max_variants": 2})
        files = converter._discover_variant_files(directory, 108)
        self.assertEqual([os.path.basename(f) for f in files], ["108.svg", "108.2.svg"])
        shutil.rmtree(directory)

    def test_disabled_returns_primary_only(self):
        directory = tempfile.mkdtemp()
        os.makedirs(os.path.join(directory, "108"))
        for name in ("108.svg", "108.2.svg"):
            open(os.path.join(directory, "108", name), "w").close()
        converter = self._converter({"enabled": False})
        files = converter._discover_variant_files(directory, 108)
        self.assertEqual([os.path.basename(f) for f in files], ["108.svg"])
        shutil.rmtree(directory)

    def test_explicit_sources_override_discovery(self):
        directory = tempfile.mkdtemp()
        os.makedirs(os.path.join(directory, "108"))
        for name in ("108.svg", "108.ignored.svg", "108.swing.svg"):
            open(os.path.join(directory, "108", name), "w").close()
        converter = self._converter({"sources": {"108": ["108.svg", "108.swing.svg"]}})
        files = converter._discover_variant_files(directory, 108)
        self.assertEqual(
            [os.path.basename(f) for f in files], ["108.svg", "108.swing.svg"]
        )
        shutil.rmtree(directory)

    def test_missing_explicit_source_raises(self):
        directory = tempfile.mkdtemp()
        os.makedirs(os.path.join(directory, "108"))
        open(os.path.join(directory, "108", "108.svg"), "w").close()
        converter = self._converter(
            {"sources": {"108": ["108.svg", "108.missing.svg"]}}
        )
        with self.assertRaises(FileNotFoundError):
            converter._discover_variant_files(directory, 108)
        shutil.rmtree(directory)

    def test_invalid_max_variants(self):
        with self.assertRaises(ValueError):
            self._converter({"max_variants": 0})._variant_settings()


@unittest.skipUnless(shutil.which("fontforge"), "fontforge is required")
@unittest.skipUnless(shutil.which("potrace"), "potrace is required")
@unittest.skipUnless(shutil.which("hb-shape"), "harfbuzz (hb-shape) is required")
class TestContextualShaping(unittest.TestCase):
    """End-to-end checks with real text shaping of the generated TTF."""

    @classmethod
    def setUpClass(cls):
        # Glyph directory with extra handwritten PNG samples:
        # codepoint 108 ("l") gets two extras, 101 ("e") one extra.
        cls.var_dir = tempfile.mkdtemp()
        SHEETtoPNG().convert(SHEET, cls.var_dir, CONFIG_FILE)
        write_variant_png(
            f"{cls.var_dir}/108/108.png",
            f"{cls.var_dir}/108/108.swing.png",
            8,
            0,
        )
        write_variant_png(
            f"{cls.var_dir}/108/108.png",
            f"{cls.var_dir}/108/108.curly.png",
            -8,
            94,
        )
        write_variant_png(
            f"{cls.var_dir}/101/101.png",
            f"{cls.var_dir}/101/101.swing.png",
            10,
            0,
        )
        PNGtoSVG().convert(directory=cls.var_dir)

        # Plain directory without extra samples.
        cls.plain_dir = tempfile.mkdtemp()
        SHEETtoPNG().convert(SHEET, cls.plain_dir, CONFIG_FILE)
        PNGtoSVG().convert(directory=cls.plain_dir)

        cls.out_dir = tempfile.mkdtemp()

        with open(CONFIG_FILE) as f:
            cls.base_config = json.load(f)

        def build(name, config):
            path = os.path.join(cls.out_dir, name + ".json")
            with open(path, "w") as f:
                json.dump(config, f)
            SVGtoTTF().convert(cls.var_dir, cls.out_dir, path, {"filename": name})
            return os.path.join(cls.out_dir, name + ".ttf")

        cls.var_ttf = build("VarFont", cls.base_config)

        manual_config = copy.deepcopy(cls.base_config)
        manual_config["typography_parameters"]["kerning_table"]["autokern"] = False
        cls.manual_ttf = build("ManualFont", manual_config)

        disabled_config = copy.deepcopy(cls.base_config)
        disabled_config["contextual_variants"]["enabled"] = False
        cls.disabled_ttf = build("DisabledFont", disabled_config)

        cap_config = copy.deepcopy(cls.base_config)
        cap_config["contextual_variants"]["max_variants"] = 2
        cap_config["contextual_variants"]["sources"] = {
            "108": ["108.svg", "108.curly.svg"],
            "101": ["101.svg", "101.swing.svg"],
        }
        cls.cap_ttf = build("CapFont", cap_config)

        SVGtoTTF().convert(
            cls.plain_dir, cls.out_dir, CONFIG_FILE, {"filename": "PlainFont"}
        )
        cls.plain_ttf = os.path.join(cls.out_dir, "PlainFont.ttf")

    @classmethod
    def tearDownClass(cls):
        for directory in (cls.var_dir, cls.plain_dir, cls.out_dir):
            shutil.rmtree(directory, ignore_errors=True)

    # ---- outline switching ------------------------------------------------

    def test_consecutive_letters_cycle_variants(self):
        names = [c[0] for c in hb_shape(self.var_ttf, "lll")]
        self.assertEqual(names, ["l", "l.v1", "l.v2"])
        names = [c[0] for c in hb_shape(self.var_ttf, "llll")]
        self.assertEqual(names, ["l", "l.v1", "l.v2", "l"])
        names = [c[0] for c in hb_shape(self.var_ttf, "llllll")]
        self.assertEqual(names, ["l", "l.v1", "l.v2"] * 2)

    def test_only_runs_of_same_letter_switch(self):
        # "level": no adjacent equal letter, every glyph stays the base.
        names = [c[0] for c in hb_shape(self.var_ttf, "level")]
        self.assertEqual(names, list("level"))
        # run in the middle of a word
        names = [c[0] for c in hb_shape(self.var_ttf, "tell")]
        self.assertEqual(names, ["t", "e", "l", "l.v1"])
        names = [c[0] for c in hb_shape(self.var_ttf, "lee")]
        self.assertEqual(names, ["l", "e", "e.v1"])

    def test_letters_without_variants_keep_base(self):
        names = [c[0] for c in hb_shape(self.var_ttf, "aaaa")]
        self.assertEqual(names, ["a"] * 4)

    # ---- determinism ------------------------------------------------------

    def test_shaping_is_deterministic(self):
        text = "little lllll eeeee level tell"
        first = hb_shape(self.var_ttf, text)
        for _ in range(3):
            self.assertEqual(hb_shape(self.var_ttf, text), first)

    def test_rebuilds_shape_identically(self):
        path = os.path.join(self.out_dir, "RebuildFont.ttf")
        if os.path.exists(path):
            os.remove(path)
        SVGtoTTF().convert(
            self.var_dir, self.out_dir, CONFIG_FILE, {"filename": "RebuildFont"}
        )
        text = "llllllllll eeeee"
        self.assertEqual(hb_shape(path, text), hb_shape(self.var_ttf, text))

    # ---- independent metrics / advance widths ----------------------------

    def test_variants_have_independent_advance_widths(self):
        w_base = raw_width(self.var_ttf, "l")
        w_v1 = raw_width(self.var_ttf, "ll")
        w_v2 = raw_width(self.var_ttf, "lll")
        self.assertNotEqual(w_v1, w_base)
        self.assertNotEqual(w_v2, w_base)
        self.assertNotEqual(w_v1, w_v2)

    def test_cluster_advances_match_glyph_widths(self):
        # The final glyph in every run carries its raw width; the same
        # variant reached through different texts keeps the same width.
        self.assertEqual(raw_width(self.var_ttf, "ll"), raw_width(self.var_ttf, "Tll"))
        # A run of three wraps back to the base glyph, same width as alone.
        self.assertEqual(raw_width(self.var_ttf, "eee"), raw_width(self.var_ttf, "e"))
        # Every cluster in a long run has a non-zero advance.
        advances = [c[1] for c in hb_shape(self.var_ttf, "l" * 12)]
        self.assertEqual(len(advances), 12)
        self.assertTrue(all(a > 0 for a in advances))

    # ---- disabling / absence of variants ---------------------------------

    def test_calt_off_restores_base_mapping(self):
        names = [c[0] for c in hb_shape(self.var_ttf, "llll", ("-calt",))]
        self.assertEqual(names, ["l"] * 4)
        names = [c[0] for c in hb_shape(self.var_ttf, "eee", ("-calt",))]
        self.assertEqual(names, ["e"] * 3)

    def test_disabled_config_maps_to_base(self):
        shaped = hb_shape(self.disabled_ttf, "llll")
        self.assertEqual([c[0] for c in shaped], ["l"] * 4)
        ids = hb_shape_ids(self.disabled_ttf, "llll")
        base_gid = hb_shape_ids(self.disabled_ttf, "l")[0][0]
        self.assertTrue(all(gid == base_gid for gid, _advance in ids))

    def test_plain_directory_has_no_substitution(self):
        names = [c[0] for c in hb_shape(self.plain_ttf, "lllleeee")]
        self.assertEqual(names, ["l"] * 4 + ["e"] * 4)
        self.assertFalse(any(".v" in name for name in names))

    # ---- kerning covers the substituted glyphs ---------------------------

    def test_autokern_covers_variant_pairs(self):
        w_l = raw_width(self.var_ttf, "l")
        w_v1 = raw_width(self.var_ttf, "ll")
        # base x variant pair is kerned
        self.assertNotEqual(hb_shape(self.var_ttf, "ll")[0][1], w_l)
        # variant x variant pair is kerned
        self.assertNotEqual(hb_shape(self.var_ttf, "lll")[1][1], w_v1)
        # variant x base pair is kerned
        self.assertNotEqual(hb_shape(self.var_ttf, "llT")[1][1], w_v1)
        # base-only pairs keep the exact same kerning as the plain font
        for text in ("To", "Av"):
            self.assertEqual(
                hb_shape(self.var_ttf, text), hb_shape(self.plain_ttf, text)
            )

    def test_manual_kerning_values_reach_variants(self):
        table = self.base_config["typography_parameters"]["kerning_table"]

        def final_class(char, classes):
            result = None
            for i, string in enumerate(classes):
                if string and char in string:
                    result = i
            return result

        matrix = table["table"][0]
        rows, cols = table["rows"], table["cols"]

        # T x l  -> table[T row][l col] on the base x base pair.
        tl = matrix[final_class("T", rows)][final_class("l", cols)]
        t_raw = raw_width(self.manual_ttf, "T")
        self.assertEqual(hb_shape(self.manual_ttf, "Tl")[0][1], t_raw + tl)

        # l.v1 x T -> same class-pair value applied through the variant
        # lookup (first glyph is a substituted variant).
        lt = matrix[final_class("l", rows)][final_class("T", cols)]
        v1_raw = raw_width(self.manual_ttf, "ll")
        self.assertEqual(hb_shape(self.manual_ttf, "llT")[1][1], v1_raw + lt)

        # l x l has a zero table value: no double application must occur.
        ll = matrix[final_class("l", rows)][final_class("l", cols)]
        self.assertEqual(ll, 0)
        self.assertEqual(
            hb_shape(self.manual_ttf, "ll")[0][1], raw_width(self.manual_ttf, "l")
        )

    # ---- rule scale bound and loop freedom --------------------------------

    def test_max_variants_caps_rules(self):
        names = [c[0] for c in hb_shape(self.cap_ttf, "l" * 10)]
        self.assertEqual(set(names), {"l", "l.v1"})
        self.assertEqual(names, ["l", "l.v1"] * 5)
        # Extras sort lexicographically, so "curly" is v1 in both fonts;
        # the cap font's single variant must be the same outline/width.
        w_curly = raw_width(self.var_ttf, "ll")
        self.assertEqual(raw_width(self.cap_ttf, "ll"), w_curly)

    def test_long_run_has_no_loop_and_stays_periodic(self):
        length = 300
        shaped = hb_shape(self.var_ttf, "l" * length)
        self.assertEqual(len(shaped), length)
        names = [c[0] for c in shaped]
        self.assertEqual(set(names), {"l", "l.v1", "l.v2"})
        self.assertFalse(any(a == b for a, b in zip(names, names[1:])))
        self.assertEqual(names[:9], ["l", "l.v1", "l.v2"] * 3)
        self.assertFalse(any(name == ".notdef" for name in names))

    def test_real_sentence(self):
        text = "little letters tell lee level"
        shaped = hb_shape(self.var_ttf, text)
        # Every character (spaces included) maps to exactly one cluster.
        self.assertEqual(len(shaped), len(text))
        self.assertFalse(any(name == ".notdef" for name, _advance in shaped))


if __name__ == "__main__":
    unittest.main()
