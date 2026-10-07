-- Lets the GitHub-flavoured Markdown in lectures/ and docs/ render as first-class Quarto pages
-- without changing the source files (GitHub must keep rendering them as-is):
--  * the first level-1 heading becomes the document title (page <title>, sidebar, search),
--    and is removed from the body so it is not shown twice. A "Day N — rest" heading is split:
--    "rest" is the title and "Day N · lecture notes" (or "lab notebook") the subtitle, which
--    site/theme.scss sets as an eyebrow above the title;
--  * ```mermaid fences (GitHub syntax) become <pre class="mermaid"> blocks; the Mermaid
--    script included from _quarto.yml renders them in the browser. Mermaid decodes the HTML
--    entities, so escaping here is safe for labels that contain < or &.
local function escape(s)
  return (s:gsub("&", "&amp;"):gsub("<", "&lt;"):gsub(">", "&gt;"))
end

local function kind_of(input)
  if input:find("/notebooks/", 1, true) then
    return "lab notebook"
  end
  return "lecture notes"
end

function Pandoc(doc)
  if doc.meta.title == nil then
    for i, block in ipairs(doc.blocks) do
      if block.t == "Header" and block.level == 1 then
        local text = pandoc.utils.stringify(block.content)
        local day, rest = text:match("^Day (%d+) [—–-]+ (.+)$")
        if day then
          doc.meta.title = pandoc.MetaString(rest)
          doc.meta.pagetitle = pandoc.MetaString("Day " .. day .. " · " .. rest)
          doc.meta.subtitle = pandoc.MetaString("Day " .. day .. " · " .. kind_of(quarto.doc.input_file or ""))
        else
          doc.meta.title = pandoc.MetaInlines(block.content)
          doc.meta.pagetitle = pandoc.MetaString(text)
        end
        table.remove(doc.blocks, i)
        break
      end
    end
  end
  return doc
end

function CodeBlock(el)
  if el.classes:includes("mermaid") then
    return pandoc.RawBlock("html", '<pre class="mermaid">\n' .. escape(el.text) .. "\n</pre>")
  end
end
