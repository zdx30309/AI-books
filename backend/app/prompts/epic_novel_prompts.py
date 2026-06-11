"""Named prompt templates for the Epic Novel Engine.

Business services should reference templates by name instead of hardcoding
large prompt strings inline.
"""
from __future__ import annotations


EPIC_PROMPT_TEMPLATES: dict[str, str] = {
    "reference_booklist_parse_prompt": "Parse a raw reference book list into candidate title/author records.",
    "reference_booklist_normalize_prompt": "Normalize reference records into tiers, corpus roles, and verification flags.",
    "sonovel_search_result_match_prompt": "Select only a high-confidence exact title/author match from SoNovel results.",
    "reference_ingest_prompt": "Extract abstract, non-prose reference mechanisms from parsed source sections.",
    "reference_pattern_extract_prompt": "Create ReferencePatternCards as abstract mechanisms; never copy source text.",
    "reference_mechanism_profile_prompt": "Aggregate ReferencePatternCards into a corpus-level mechanism profile.",
    "reference_mechanism_profile_review_prompt": "Review profile for overfitting, single-title anchoring, and copy risk.",
    "auto_retrieval_planner_prompt": "Decide whether retrieval is needed and which abstract cards should guide the task.",
    "reference_inspiration_pack_prompt": "Build a constrained inspiration pack from cards/profile only.",
    "plot_function_contract_prompt": "Convert story intent into a plot-function contract.",
    "worldview_tournament_prompt": "Generate multiple original worldview candidates from project intent and abstract patterns.",
    "worldview_judge_prompt": "Judge worldview candidates for originality, longform capacity, and genre promise.",
    "world_atlas_prompt": "Design original nations, regions, factions, institutions, and map conflicts.",
    "bureaucracy_design_prompt": "Transform historical inspiration into original fantasy bureaucracy systems.",
    "power_system_forge_prompt": "Forge an original power system with progression, costs, limits, and plot functions.",
    "power_system_stress_test_prompt": "Find loopholes, power creep, and anticlimactic progression risks.",
    "ensemble_cast_prompt": "Generate an ensemble cast with independent heroine arcs and faction functions.",
    "global_spine_prompt": "Design the hidden truth, global conflict, and longform reveal path.",
    "bible_manifest_prompt": "Plan Master Bible sections, dependencies, coverage tags, and target word counts.",
    "bible_section_prompt": "Generate one Master Bible section from approved planning data and abstract references.",
    "bible_section_check_prompt": "Check a Bible section for coverage, contradictions, and missing dependencies.",
    "bible_coverage_ci_prompt": "Evaluate Master Bible coverage across required sections and story systems.",
    "bible_repair_prompt": "Patch only the affected Bible section; do not rewrite the whole Bible.",
    "volume_contract_prompt": "Generate volume contracts that serve global spine, world atlas, bureaucracy, and arcs.",
    "character_arc_grid_prompt": "Build character arc grids with independent goals, choices, costs, and endings.",
    "foreshadow_ledger_prompt": "Track setup, echo, payoff, and global-spine links.",
    "reader_question_ledger_prompt": "Track reader questions, partial reveals, false answers, and true answers.",
    "pleasure_curve_prompt": "Plan intellectual pressure, clue payoffs, reversals, and reader rewards.",
    "outline_ci_prompt": "Check volume/outline execution against spine, atlas, arcs, institutions, and pleasure curve.",
    "outline_repair_prompt": "Patch only local outline defects without a full rewrite.",
    "chapter_contract_prompt": "Compile a chapter contract with required events, constraints, and ending hook.",
    "chapter_context_pack_prompt": "Assemble the chapter context pack from canon, state, Bible, atlas, and reference cards.",
    "chapter_write_prompt": "Write chapter draft from Chapter Contract and Context Pack; do not update canon.",
    "state_extract_prompt": "Extract durable state from accepted content only.",
    "planned_vs_actual_diff_prompt": "Compare accepted chapter content against the Chapter Contract.",
    "chapter_acceptance_prompt": "Gate acceptance; high or severe issues block accepted canon writes.",
    "repair_chapter_prompt": "Patch a chapter locally to address specific acceptance issues.",
}


def get_epic_prompt(name: str) -> str:
    try:
        return EPIC_PROMPT_TEMPLATES[name]
    except KeyError as exc:
        raise KeyError(f"Unknown Epic prompt template: {name}") from exc


def list_epic_prompt_names() -> list[str]:
    return sorted(EPIC_PROMPT_TEMPLATES)
