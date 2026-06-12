# Master Bible Content Matrix

Updated: 2026-06-12

Status: `PASS IN CURRENT STRICT ACCEPTANCE`

The service now defines 24 Master Bible sections. A section is production-pass only when:

- `EpicBibleSection.status == accepted`
- `content_json.acceptance.status == pass`
- `_meta.production_quality == true`
- `rawTextIncluded == false`
- no fallback/placeholder marker is present.

| Order | Section key | Required content | Current implementation | Current live status |
| ---: | --- | --- | --- | --- |
| 1 | `project_overview` | project promise, longform capacity, originality boundary | model-driven section generator + validator | accepted in current partial project |
| 2 | `genre_promise` | reader promises, pleasure curve, anti-cliche rules | model-driven section generator + validator | accepted in current partial project |
| 3 | `world_law` | core world rules, costs, violations | model-driven section generator + validator | accepted in current partial project |
| 4 | `power_system` | progression, limits, cost, realm logic | model-driven section generator + validator | accepted in current partial project |
| 5 | `nation_and_court` | state/court/bureaucracy power structure | model-driven section generator + validator | accepted |
| 6 | `bureaucracy` | workflow, institutions, obstacles, corruption channels | model-driven section generator + validator | accepted |
| 7 | `map_geography` | regions, movement rules, map pressure | model-driven section generator + validator | accepted in current partial project |
| 8 | `factions` | factions, goals, conflicts, resources | model-driven section generator + validator | accepted |
| 9 | `characters` | protagonist, ensemble, antagonists, arc functions | model-driven section generator + validator | accepted |
| 10 | `heroine_arcs` | heroine agency, independent goals/resources/costs | model-driven section generator + validator | accepted |
| 11 | `relationship_graph` | trust, opposition, alliance tension | model-driven section generator + validator | accepted |
| 12 | `history_timeline` | backstory, hidden debt, public history | model-driven section generator + validator | accepted |
| 13 | `global_spine` | long arc, mystery ladder, volume escalation | model-driven section generator + validator | accepted |
| 14 | `volume_contracts` | volume promises and escalation contracts | model-driven section generator + validator | accepted |
| 15 | `foreshadow_ledger` | plants, echoes, payoff timing | model-driven section generator + validator | accepted |
| 16 | `reader_question_ledger` | open/answer/escalate reader questions | model-driven section generator + validator | accepted |
| 17 | `pleasure_curve` | rhythm of payoffs, reversals, upgrades | model-driven section generator + validator | accepted |
| 18 | `villain_plan` | antagonist agency, resources, timeline | model-driven section generator + validator | accepted |
| 19 | `style_guide` | Simplified Chinese prose and scene rules | model-driven section generator + validator | accepted |
| 20 | `anti_cliche_rules` | no copying, no no-cost upgrades, no passive heroines | model-driven section generator + validator | accepted |
| 21 | `ending_plan` | ending direction and payoff constraints | model-driven section generator + validator | accepted |
| 22 | `glossary` | controlled project terms | model-driven section generator + validator | accepted |
| 23 | `reference_profile_usage` | how ReferenceMechanismProfile can be used safely | model-driven section generator + validator | accepted |
| 24 | `production_control_rules` | serial chapter execution, context/export/state rules | model-driven section generator + validator | accepted |

## Passing Evidence

Strict smoke project `44ce72be-a423-4f3b-895b-ad8a0fbb5e63` generated 24 accepted Bible sections and 24 complete coverage items. Passing log: `.local/epic_novel_runs/acceptance_smoke_strict_2026-06-12-resume4.log`.
