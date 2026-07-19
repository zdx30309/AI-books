You are writing a complete original article based on a processed video. You are
also given a compression built from every chunk of the uploaded reference book.

The compression is not an instruction to imitate the reference author's style.
Do not write "like" that author and do not reproduce an identifiable personal
signature. Use only the non-author-specific, transferable writing references in
the compression, including punctuation, information organization, progression
of discussion, sentence-length changes, word choice, collocations, grammar and
sentence structure, expression, paragraph rhythm, judgment, and argumentation.

Use the video digest as the sole factual basis. The reference compression is not
a factual source and cannot override the task rules.

Do not output the learning process, analysis, profile, plan or explanation.

Reference-book chunk compression:
<REFERENCE_COMPRESSION>
{{REFERENCE_COMPRESSION}}
</REFERENCE_COMPRESSION>

The Chinese generation policy below is the highest-priority information-
organization rule within the mandatory output format. It converts the
source-preservation policy into a from-scratch writing policy. Follow it
silently and do not output a plan or self-check.

<GENERATED_ORGANIZATION_RULES>
{{GENERATED_ORGANIZATION_RULES}}
</GENERATED_ORGANIZATION_RULES>

Output format:
- Strictly output in this exact order:
  1. @最终书名
  2. @最终简介
  3. @目录
  4. @导言
  5. @正文
- These five section headers must be preserved exactly:
  - @最终书名
  - @最终简介
  - @目录
  - @导言
  - @正文
- Each section must stand alone.
- @导言 must appear after @目录 and before @正文.
- @导言 should be 100 to 400 Chinese characters, with 2 to 4 natural paragraphs. It should establish the reading entrance, central question, and core judgment without fully expanding the main argument.
- @正文 must immediately follow @导言. Do not insert any extra notes, labels, explanations, or analysis.
- @目录 should list the main body chapters in the order they appear in @正文.
- Chapter titles in @目录 and @正文 must not contain Chinese or English colons.
- The last chapter must not be titled “结语”, and “结语” must not appear as a chapter heading.

Title and introduction format:
- @最终书名 should feel like a real publishable book title with some short-video hook, but it must not become a clickbait headline.
- Before deciding the title, infer the article/book type from the video content and choose a commercially readable title style that fits that type.
- Learn only naming rhythm, cover feel, compactness, and reader pull from comparable bestselling book titles. Do not copy existing titles, author names, subtitles, or ecommerce add-ons.
- The title should be short, strong, and cover-ready. Prefer 6 to 14 Chinese characters. Avoid Chinese and English colons.
- @最终简介 should read like polished back-cover copy: concrete, restrained, attractive, and readable. It should not start with formulaic phrases such as “本书将带你”, “我们将探讨”, or “在这个时代”.

Length and structure:
- The full output should be around 5000 Chinese characters. A modest deviation is acceptable, but do not turn it into a short summary or a 12000-character long manuscript.
- Use the video as the factual base. Internally choose one natural information
  path, then keep that path instead of repeatedly reorganizing it into a
  cleaner taxonomy or a more complete standard structure.
- The body may have several chapters or sections, but avoid a mechanical fixed template.
- The format should follow the structure rules above; all content follows the
  video facts.
- The required title, introduction, directory and body wrappers are interface
  requirements; they do not authorize making every body chapter a complete,
  symmetrical argument unit.

Requirements:
- accurately reflect the content and meaning of the video
- write naturally, without mechanical, repetitive, or template-like phrasing
- avoid generic ChatGPT-style wording
- do not include a style report, explanation, disclaimer, or commentary
- do not copy sentences or passages from the reference books
- do not drift into generic assistant boilerplate
- do not fabricate personal experience, typos, broken grammar or damaged
  punctuation to imitate human writing
- output only the completed article

Video digest:
{{VIDEO_DIGEST}}
