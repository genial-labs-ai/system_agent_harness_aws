-- Lets the GitHub-flavoured Markdown in lectures/ and docs/ render as first-class Quarto pages
-- without changing the source files (GitHub must keep rendering them as-is):
--  * the first level-1 heading becomes the document title (page <title>, sidebar, search),
--    and is removed from the body so it is not shown twice;
--  * ```mermaid fences (GitHub syntax) become <pre class="mermaid"> blocks; the Mermaid
--    script included from _quarto.yml renders them in the browser. Mermaid decodes the HTML
--    entities, so escaping here is safe for labels that contain < or &.
local function escape(s)
  return (s:gsub("&", "&amp;"):gsub("<", "&lt;"):gsub(">", "&gt;"))
end

function Pandoc(doc)
  if doc.meta.title == nil then
    for i, block in ipairs(doc.blocks) do
      if block.t == "Header" and block.level == 1 then
        doc.meta.title = pandoc.MetaInlines(block.content)
        doc.meta.pagetitle = pandoc.MetaString(pandoc.utils.stringify(block.content))
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
