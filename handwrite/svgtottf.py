import sys
import os
import glob
import json
import uuid


class SVGtoTTF:
    def convert(self, directory, outdir, config, metadata=None):
        """Convert a directory with SVG images to TrueType Font.

        Calls a subprocess to the run this script with Fontforge Python
        environment.

        Parameters
        ----------
        directory : str
            Path to directory with SVGs to be converted.
        outdir : str
            Path to output directory.
        config : str
            Path to config file.
        metadata : dict
            Dictionary containing the metadata (filename, family or style)
        """
        import subprocess
        import platform

        subprocess.run(
            (
                ["ffpython"]
                if platform.system() == "Windows"
                else ["fontforge", "-script"]
            )
            + [
                os.path.abspath(__file__),
                config,
                directory,
                outdir,
                json.dumps(metadata),
            ]
        )

    def set_properties(self):
        """Set metadata of the font from config."""
        props = self.config["props"]
        lang = props.get("lang", "English (US)")
        fontname = self.metadata.get("filename", None) or props.get(
            "filename", "Example"
        )
        family = self.metadata.get("family", None) or fontname
        style = self.metadata.get("style", None) or props.get("style", "Regular")

        self.font.familyname = fontname
        self.font.fontname = fontname + "-" + style
        self.font.fullname = fontname + " " + style
        self.font.encoding = props.get("encoding", "UnicodeFull")

        for k, v in props.items():
            if hasattr(self.font, k):
                if isinstance(v, list):
                    v = tuple(v)
                setattr(self.font, k, v)

        if self.config.get("sfnt_names", None):
            self.config["sfnt_names"]["Family"] = family
            self.config["sfnt_names"]["Fullname"] = family + " " + style
            self.config["sfnt_names"]["PostScriptName"] = family + "-" + style
            self.config["sfnt_names"]["SubFamily"] = style

        self.config["sfnt_names"]["UniqueID"] = family + " " + str(uuid.uuid4())

        for k, v in self.config.get("sfnt_names", {}).items():
            self.font.appendSFNTName(str(lang), str(k), str(v))

    def _variant_settings(self):
        """Return (enabled, max_variants, sources) from the config.

        Multiple handwritten samples for one codepoint can be registered
        either through the glyph directory (``<codepoint>/<codepoint>.*.svg``)
        or explicitly through the ``contextual_variants.sources`` mapping,
        e.g. ``{"108": ["108.svg", "108.alt.svg"]}``.
        """
        settings = self.config.get("contextual_variants", {}) or {}
        enabled = bool(settings.get("enabled", True))
        max_variants = int(settings.get("max_variants", 4))
        if max_variants < 1:
            raise ValueError("contextual_variants.max_variants must be >= 1")
        sources = {}
        for codepoint, files in (settings.get("sources", {}) or {}).items():
            sources[int(codepoint)] = list(files)
        return enabled, max_variants, sources

    def _discover_variant_files(self, directory, codepoint):
        """List the SVG samples registered for one codepoint.

        The primary sample is always ``<codepoint>/<codepoint>.svg`` and is
        returned first. Extra samples are discovered deterministically as
        ``<codepoint>/<codepoint>.*.svg`` (sorted by filename), or taken from
        the explicit ``sources`` config entry when present. The total number
        of samples is capped by ``contextual_variants.max_variants``.

        Parameters
        ----------
        directory : str
            Path to the directory with the SVG subdirectories.
        codepoint : int
            Unicode codepoint of the character.

        Returns
        -------
        list of str
            SVG paths, primary first, stable order.
        """
        enabled, max_variants, sources = self._variant_settings()
        char_dir = os.path.join(directory, str(codepoint))
        primary = os.path.join(char_dir, "{}.svg".format(codepoint))
        if not enabled:
            return [primary]
        if codepoint in sources:
            files = [os.path.join(char_dir, name) for name in sources[codepoint]]
            for path in files:
                if not os.path.exists(path):
                    raise FileNotFoundError(
                        "Contextual variant sample not found: {}".format(path)
                    )
        else:
            extras = sorted(
                glob.glob(os.path.join(char_dir, "{}.*.svg".format(codepoint))),
                key=os.path.basename,
            )
            files = [primary] + extras
        if len(files) > max_variants:
            sys.stderr.write(
                "Codepoint {} registers {} samples, keeping the first "
                "{} (contextual_variants.max_variants)\n".format(
                    codepoint, len(files), max_variants
                )
            )
            files = files[:max_variants]
        return files

    def add_glyphs(self, directory):
        """Read and add SVG images as glyphs to the font.

        Walks through the provided directory and uses each ord(character).svg file
        as glyph for the character. Additional ``<codepoint>.*.svg`` samples are
        imported as unmapped variant glyphs with stable names
        ``<glyphname>.v1``, ``<glyphname>.v2`` ... Each variant keeps its own
        outline and its own metrics. Then using the provided config, set the
        font parameters and export TTF file to outdir.

        Parameters
        ----------
        directory : str
            Path to directory with SVGs to be converted.
        """
        space = self.font.createMappedChar(ord(" "))
        space.width = 500

        # codepoint -> [variant glyph names] (stable order)
        self.variant_mapping = {}

        for k in self.config["glyphs"]:
            # Create character glyph
            g = self.font.createMappedChar(k)
            base_name = g.glyphname
            self.unicode_mapping.setdefault(k, base_name)
            # Get all registered samples (primary outline first)
            samples = self._discover_variant_files(directory, k)
            g.importOutlines(samples[0], ("removeoverlap", "correctdir"))
            g.removeOverlap()
            variant_names = []
            for index, sample in enumerate(samples[1:], start=1):
                variant_name = "{}.v{}".format(base_name, index)
                variant = self.font.createChar(-1, variant_name)
                variant.importOutlines(sample, ("removeoverlap", "correctdir"))
                variant.removeOverlap()
                variant_names.append(variant_name)
            if variant_names:
                self.variant_mapping[k] = variant_names

    def set_bearings(self, bearings):
        """Add left and right bearing from config

        Parameters
        ----------
        bearings : dict
            Map from character: [left bearing, right bearing]
        """
        default = bearings.get("Default", [60, 60])

        for k, v in bearings.items():
            if v[0] is None:
                v[0] = default[0]
            if v[1] is None:
                v[1] = default[1]

            if k != "Default":
                codepoint = ord(str(k))
                glyph_name = self.unicode_mapping[codepoint]
                glyph_names = [glyph_name] + self.variant_mapping.get(codepoint, [])
                # Every glyph of a codepoint (base + variants) gets its own
                # independent bearing record.
                for name in glyph_names:
                    self.font[name].left_side_bearing = v[0]
                    self.font[name].right_side_bearing = v[1]

    def set_contextual_variants(self):
        """Build the contextual substitution that cycles variant outlines.

        For every codepoint with extra samples, runs of the same character
        cycle through the variants deterministically: the second glyph in a
        run becomes ``.v1``, the third ``.v2`` and so on, wrapping around.
        Rules only ever match the *base* glyph, substituted variant glyphs
        can never be matched again, so the lookup is acyclic (it cannot loop)
        and leaves the cmap untouched. Disabling the feature or providing no
        extra samples results in plain character mapping.

        Rule count is bounded by
        ``len(glyphs) * (max_variants - 1)``.
        """
        enabled, _, _ = self._variant_settings()
        if not enabled:
            return

        rules = []
        last_lookup = None
        for k in self.config["glyphs"]:
            variants = self.variant_mapping.get(k)
            if not variants:
                continue
            base = self.unicode_mapping[k]
            for index, variant_name in enumerate(variants, start=1):
                # One single-substitution lookup per transition, referenced
                # from the contextual lookup but attached to no feature.
                lookup_name = "vs-{}-{}".format(base, index)
                subtable_name = lookup_name + "-sub"
                self.font.addLookup(lookup_name, "gsub_single", 0, ())
                self.font.addLookupSubtable(lookup_name, subtable_name)
                self.font[base].addPosSub(subtable_name, variant_name)
                predecessor = base if index == 1 else variants[index - 2]
                rules.append(
                    (
                        "calt-{}-{}".format(base, index),
                        "{} | {} @<{}>".format(predecessor, base, lookup_name),
                    )
                )
                last_lookup = lookup_name

        if not rules:
            return

        # calt is enabled by default by shapers; turning it off restores the
        # original mapping because variants are not reachable any other way.
        feature = (("calt", (("latn", ("dflt",)),)),)
        self.font.addLookup("calt", "gsub_contextchain", 0, feature, last_lookup)
        for subtable_name, rule in rules:
            self.font.addContextualSubtable("calt", subtable_name, "glyph", rule)

    def _kerning_class_members(self, chars, mode):
        """Resolve a config kerning class to concrete glyph names.

        Parameters
        ----------
        chars : list of str
            Single characters of one kerning class (as split from the config
            strings).
        mode : str
            ``"b"`` includes base glyphs, ``"v"`` variant glyphs and
            ``"bv"`` both.

        Returns
        -------
        tuple of str
            Glyph names participating in that class. A character is included
            only when its glyph name is the character itself, mirroring how
            Fontforge resolves the base kerning classes.
        """
        names = []
        for char in chars:
            codepoint = ord(char)
            base = self.unicode_mapping.get(codepoint)
            if base != char:
                continue
            if mode in ("b", "bv"):
                names.append(base)
            if mode in ("v", "bv"):
                names.extend(self.variant_mapping.get(codepoint, ()))
        return tuple(names)

    def _add_variant_kerning_lookup(
        self,
        lookup,
        rows,
        cols,
        row_mode,
        col_mode,
        separation,
        offsets,
        after="kern",
    ):
        """Add a kerning lookup covering pairs involving variant glyphs.

        Each call creates a *separate* ``gpos_pair`` lookup registered for
        the ``dist`` feature: pair positioning subtables within one lookup
        stop at the first covering subtable, so the variant pairs must not
        share the base lookup. Separate lookups accumulate, and their
        coverages never overlap (variant-first vs base-first).

        Class indices and values mirror the base ``kern-1`` table. Empty
        classes are dropped (the implicit class 0 always gets zero values),
        so the extra lookups never overlap with ``kern-1`` (base/base
        pairs) nor each other.
        """
        kept_rows = []
        for i, chars in enumerate(rows):
            if i == 0 or chars is None:
                continue
            members = self._kerning_class_members(chars, row_mode)
            if members:
                kept_rows.append((i, members))
        kept_cols = []
        for j, chars in enumerate(cols):
            if j == 0 or chars is None:
                continue
            members = self._kerning_class_members(chars, col_mode)
            if members:
                kept_cols.append((j, members))
        if not kept_rows or not kept_cols:
            return

        new_rows = [None] + [members for _, members in kept_rows]
        new_cols = [None] + [members for _, members in kept_cols]

        # A separate feature tag is required: Fontforge merges pair lookups
        # sharing the same feature into one lookup at generation time, which
        # would make subtable matching short-circuit. "dist" (distances) is
        # applied by default by shapers just like "kern".
        feature = [["dist", [["latn", ["dflt"]]]]]
        self.font.addLookup(lookup, "gpos_pair", 0, feature, after)

        if offsets is None:
            self.font.addKerningClass(
                lookup, lookup + "-1", separation, new_rows, new_cols, True
            )
        else:
            n_cols = len(new_cols)
            original_cols = len(cols)
            new_offsets = [0] * (len(new_rows) * n_cols)
            # Class 0 row/column stay zero; interior values keep the same
            # class pair values as the base kerning table.
            for ri, (original_row, _) in enumerate(kept_rows, start=1):
                for ci, (original_col, _) in enumerate(kept_cols, start=1):
                    new_offsets[ri * n_cols + ci] = offsets[
                        original_row * original_cols + original_col
                    ]
            self.font.addKerningClass(
                lookup, lookup + "-1", new_rows, new_cols, new_offsets
            )

    def set_kerning(self, table):
        """Set kerning values in the font.

        Additional pair-positioning lookups extend the configured kerning
        classes to the variant glyphs so that pairs remain kerned after
        contextual substitution.

        Parameters
        ----------
        table : dict
            Config dictionary with kerning values/autokern bool.
        """
        rows = table["rows"]
        rows = [list(i) if i != None else None for i in rows]
        cols = table["cols"]
        cols = [list(i) if i != None else None for i in cols]

        self.font.addLookup("kern", "gpos_pair", 0, [["kern", [["latn", ["dflt"]]]]])

        if table.get("autokern", True):
            separation = table.get("seperation", 0)
            self.font.addKerningClass("kern", "kern-1", separation, rows, cols, True)
            # Rows: variants ; cols: base + variants  -> v/b and v/v pairs
            self._add_variant_kerning_lookup(
                "kern-vx", rows, cols, "v", "bv", separation, None
            )
            # Rows: base ; cols: variants             -> b/v pairs
            self._add_variant_kerning_lookup(
                "kern-bv", rows, cols, "b", "v", separation, None, "kern-vx"
            )
        else:
            kerning_table = table.get("table", False)
            if not kerning_table:
                raise ValueError("Kerning offsets not found in the config file.")
            flatten_list = lambda y: (
                [x for a in y for x in flatten_list(a)] if type(y) is list else [y]
            )
            offsets = [0 if x is None else x for x in flatten_list(kerning_table)]
            self.font.addKerningClass("kern", "kern-1", rows, cols, offsets)
            self._add_variant_kerning_lookup(
                "kern-vx", rows, cols, "v", "bv", None, offsets
            )
            self._add_variant_kerning_lookup(
                "kern-bv", rows, cols, "b", "v", None, offsets, "kern-vx"
            )

    def generate_font_file(self, filename, outdir, config_file):
        """Output TTF file.

        Additionally checks for multiple outputs and duplicates.

        Parameters
        ----------
        filename : str
            Output filename.
        outdir : str
            Path to output directory.
        config_file : str
            Path to config file.
        """
        if filename is None:
            raise NameError("filename not found in config file.")

        outfile = str(
            outdir
            + os.sep
            + (filename + ".ttf" if not filename.endswith(".ttf") else filename)
        )

        while os.path.exists(outfile):
            outfile = os.path.splitext(outfile)[0] + " (1).ttf"

        sys.stderr.write("\nGenerating %s...\n" % outfile)
        self.font.generate(outfile)

    def convert_main(self, config_file, directory, outdir, metadata):
        try:
            self.font = fontforge.font()
        except:
            import fontforge

        with open(config_file) as f:
            self.config = json.load(f)
        self.metadata = json.loads(metadata) or {}

        self.font = fontforge.font()
        self.unicode_mapping = {}
        self.set_properties()
        self.add_glyphs(directory)

        # bearing table
        self.set_bearings(self.config["typography_parameters"].get("bearing_table", {}))

        # contextual alternates cycling through variant samples
        self.set_contextual_variants()

        # kerning table (also covers substituted variant glyphs)
        self.set_kerning(self.config["typography_parameters"].get("kerning_table", {}))

        # Generate font and save as a .ttf file
        filename = self.metadata.get("filename", None) or self.config["props"].get(
            "filename", None
        )
        self.generate_font_file(str(filename), outdir, config_file)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise ValueError("Incorrect call to SVGtoTTF")
    SVGtoTTF().convert_main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
