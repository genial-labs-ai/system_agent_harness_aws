# Fonts

Three variable-font files are vendored here so the website does not depend on a font CDN. All are
latin subsets downloaded from [Fontsource](https://fontsource.org/) (jsDelivr mirror).

| File | Family | Licence |
|---|---|---|
| `inter-latin-wght-normal.woff2` | [Inter](https://github.com/rsms/inter) | SIL Open Font License 1.1 |
| `source-serif-4-latin-wght-normal.woff2` | [Source Serif 4](https://github.com/adobe-fonts/source-serif) | SIL Open Font License 1.1 |
| `source-serif-4-latin-wght-italic.woff2` | [Source Serif 4](https://github.com/adobe-fonts/source-serif) | SIL Open Font License 1.1 |

The `@font-face` rules are in `fonts.css`, which `_quarto.yml` loads as a plain stylesheet.
The licence and copyright notices are redistributed alongside, as the OFL requires:
`LICENSE-Inter.txt` (The Inter Project Authors) and `LICENSE-SourceSerif4.md` (Adobe).
