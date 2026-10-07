-- Lets the GitHub-flavoured Markdown in lectures/ and docs/ render as first-class Quarto pages
-- without changing the source files (GitHub must keep rendering them as-is):
--  * the first level-1 heading becomes the document title (page <title>, sidebar, search),
--    and is removed from the body so it is not shown twice. A "Day N — rest" heading is split:
--    "rest" (with its inline formatting) is the title and "Day N · lecture notes" (or
--    "lab notebook") the subtitle, which site/theme.scss sets as an eyebrow above the title.
--    Notebooks arrive with `title` already set by Quarto's jupyter engine (from the same first
--    heading), so the split is applied to the metadata title as well;
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

-- "Day N — rest" as a list of inlines: returns N and the inlines after the dash, or nil.
local function split_day(inlines)
  if #inlines < 5 or inlines[1].t ~= "Str" or inlines[1].text ~= "Day" then
    return nil
  end
  if inlines[2].t ~= "Space" or inlines[3].t ~= "Str" or not inlines[3].text:match("^%d+$") then
    return nil
  end
  if inlines[4].t ~= "Space" or inlines[5].t ~= "Str" or not inlines[5].text:match("^[—–-]+$") then
    return nil
  end
  local rest = pandoc.Inlines({})
  for i = 6, #inlines do
    if not (i == 6 and inlines[i].t == "Space") then
      rest:insert(inlines[i])
    end
  end
  if #rest == 0 then
    return nil
  end
  return inlines[3].text, rest
end

-- Applies the split to the document metadata; returns false when there is nothing to split.
local function set_split_title(doc, inlines)
  local day, rest = split_day(inlines)
  if day == nil then
    return false
  end
  doc.meta.title = pandoc.MetaInlines(rest)
  doc.meta.pagetitle = pandoc.MetaString("Day " .. day .. " · " .. pandoc.utils.stringify(rest))
  doc.meta.subtitle = pandoc.MetaString("Day " .. day .. " · " .. kind_of(quarto.doc.input_file or ""))
  return true
end

function Pandoc(doc)
  if doc.meta.title == nil then
    for i, block in ipairs(doc.blocks) do
      if block.t == "Header" and block.level == 1 then
        if not set_split_title(doc, block.content) then
          doc.meta.title = pandoc.MetaInlines(block.content)
          doc.meta.pagetitle = pandoc.MetaString(pandoc.utils.stringify(block.content))
        end
        table.remove(doc.blocks, i)
        break
      end
    end
  elseif doc.meta.subtitle == nil and pandoc.utils.type(doc.meta.title) == "Inlines" then
    -- A document whose title is already set (a notebook): only a "Day N — rest" title changes.
    set_split_title(doc, doc.meta.title)
  end
  return doc
end

function CodeBlock(el)
  if el.classes:includes("mermaid") then
    return pandoc.RawBlock("html", '<pre class="mermaid">\n' .. escape(el.text) .. "\n</pre>")
  end
end
