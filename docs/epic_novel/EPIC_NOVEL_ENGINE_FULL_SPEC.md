# Epic Novel Engine Full Spec

You are my senior full-stack engineer, system architect, and AI production-system engineer.

Build a complete “300万字级别长篇网文工业生成引擎” on top of the existing project.

The existing project already has short-form fiction/content generation infrastructure. Do not remove it. Do not rewrite the project. The existing short-form generator must become the Chapter Draft Writer.

The new system must add a long-form industrial control plane:

- Model Router
- Storage & State Layer
- Reference Booklist Layer
- External Downloader Adapter Layer
- SoNovel Adapter
- Reference Raw Text Import Layer
- Reference RAG Layer
- Reference Mechanism Profile Layer
- Auto Retrieval Planner
- Worldbuilding Atlas Engine
- Mega Bible Compiler
- Genesis Layer
- Planning Layer
- Execution Layer
- QA / Acceptance Layer
- Production Dashboard
- Export System

Target genre for v1:

古代穿越 · 悬疑探案 · 朝堂权谋 · 仙侠志怪 · 群像 · 多女主 · 智力压制 · 大局小局 · 草蛇灰线 · 创新战力体系。

Core principle:

Do not hardcode any single reference work as an anchor.

The full 100-book reference corpus is the reference source. The engine must infer a ReferenceMechanismProfile from the entire corpus, not from one title.

The system must not copy:
- plot
- characters
- names
- scenes
- prose
- jokes
- chapter structure
- exact twists
- unique institutions
- unique power systems

Allowed extraction:
- plot function
- pleasure mechanism
- mystery structure
- foreshadow pattern
- character arc pattern
- heroine agency pattern
- bureaucracy conflict pattern
- worldbuilding function
- power progression logic
- anti-cliche rules

==================================================
0. Repo Inspection
==================================================

Before coding, inspect the repo and identify:

- frontend framework
- backend framework
- database/ORM
- existing LLM client
- existing prompt/template system
- existing short-form generation flow
- existing long-form generation flow if any
- existing task/queue system if any
- existing export system
- existing logging/cost system
- existing state extraction/continuity checker if any

Use the existing project style. Do not introduce unnecessary large frameworks. Reuse existing LLM client, prompt loading, routing, task, export, and frontend conventions where possible.

Write a short implementation plan before coding.

==================================================
1. Non-Negotiable Rules
==================================================

Must follow:

1. Do not break existing short-form generation.
2. Do not rewrite the whole project.
3. Existing short-form generator must be reused as Chapter Draft Writer.
4. Do not hardcode prompts inside business logic.
5. Do not hardcode any single reference title as target anchor.
6. Do not put raw reference-book text directly into generation prompts.
7. RAG must use abstract ReferencePatternCards only.
8. Do not build a new unrestricted crawler inside this project.
9. Use the user's local SoNovel tool through an adapter when REFERENCE_DOWNLOADER_PROVIDER=sonovel.
10. External downloader output must be treated as raw ingestion input only.
11. Raw text must never be used directly as generation RAG.
12. Chapters must generate strictly serially.
13. Never generate chapters in parallel.
14. draftContent must never write into canon/state ledgers.
15. repairedContent must not directly write into canon/state ledgers.
16. Only acceptedContent can persist accepted canon state.
17. High/severe issues block acceptance.
18. Outline repair must be local patch, not full rewrite.
19. Bible repair must be section-level patch, not full rewrite.
20. Every chapter must run Planned vs Actual Diff and Acceptance Gate.
21. Master Bible, Master Outline, and Chapter Contract are different layers and must not be mixed.
22. Update docs/epic_novel/IMPLEMENTATION_STATUS.md after each major phase.
23. All LLM calls must be logged with model, action, cost, status, and errors.
24. All reference downloads/imports must record provider, query, local path, status, error, file hash, and generated card count.
25. Raw reference text may be parsed and abstracted, but generation prompts must only receive ReferencePatternCards or ReferenceMechanismProfile summaries.

==================================================
2. Model Router
==================================================

Add or extend LLM Router with three model roles:

editor_model:
Default intended use: GPT-5.5 level.
Used for:
- worldbuilding final review
- power-system final review
- reference mechanism profile review
- global spine design
- volume contract review
- character arc review
- Bible Coverage CI
- Outline CI
- high-risk repairs
- volume-level final review
- ending design

author_model:
Default intended use: DeepSeek V4 Pro level.
Used for:
- Bible section generation
- volume draft planning
- chapter contracts
- normal chapter drafting
- outline patch
- scene expansion
- emotional scene improvement
- normal repairs

cheap_model:
Default intended use: DeepSeek Flash or cheapest reliable model.
Used for:
- JSON extraction
- summaries
- state extraction
- reference pattern extraction
- booklist normalization
- cheap checks
- logs
- simple QA

Environment variables:

EPIC_EDITOR_MODEL_PROVIDER=
EPIC_EDITOR_MODEL_NAME=
EPIC_AUTHOR_MODEL_PROVIDER=
EPIC_AUTHOR_MODEL_NAME=
EPIC_CHEAP_MODEL_PROVIDER=
EPIC_CHEAP_MODEL_NAME=

EPIC_MAX_PROJECT_COST=
EPIC_MAX_CHAPTER_REPAIR_COUNT=1
EPIC_ENABLE_EDITOR_REVIEW=true
EPIC_ENABLE_REFERENCE_RAG=true
EPIC_ENABLE_WORLD_ATLAS=true
EPIC_ENABLE_MEGA_BIBLE=true
EPIC_ENABLE_REFERENCE_PROFILE=true

Every LLM call must record:
- provider
- model
- action
- projectId
- bibleId nullable
- sectionKey nullable
- volumeNumber nullable
- chapterNumber nullable
- promptTokens
- completionTokens
- estimatedCost
- status
- error
- createdAt

==================================================
3. Local Storage & State Layer
==================================================

This engine is a long-form novel state database, not a simple article generator.

The system must distinguish six kinds of data:

1. Planning Data:
- EpicMasterBible
- EpicBibleSection
- EpicWorldBible
- EpicWorldAtlas
- EpicVolumeContract
- EpicChapterContract
- EpicCharacterArc
- EpicForeshadow
- EpicReaderQuestion
- EpicPleasureBeat

2. Draft Text:
- EpicChapter.draftContent
- EpicChapter.repairedContent
- rawModelOutput

3. Accepted Canon State:
- EpicChapter.acceptedContent
- EpicCanonFact
- EpicCharacterState
- EpicPowerLedger
- EpicObjectState
- EpicFactionState
- EpicInstitutionState
- EpicStateExtract

4. Retrieval Traces:
- EpicRetrievalTrace
- ReferencePatternCard
- ReferenceMechanismProfile
- ReferenceInspirationPack metadata

5. QA Reports:
- EpicIssue
- EpicAcceptanceReport
- Outline CI report
- Bible Coverage CI report

6. Export Artifacts:
- Master Bible markdown/json/docx
- Full novel markdown/txt/json
- Volume exports
- large debug context packs
- large raw model outputs

Canon write rules:
- draftContent is only draft.
- repairedContent is only a candidate.
- Only acceptedContent can update:
  - EpicCanonFact
  - EpicCharacterState
  - EpicPowerLedger
  - EpicForeshadow
  - EpicReaderQuestion
  - EpicPleasureBeat
  - EpicFactionState
  - EpicInstitutionState
  - EpicObjectState

Local storage paths:

.local/novel_ref_corpus/
  booklist_seed.md
  booklist.csv
  booklist.json
  raw/
    tier_1_core_similarity/
    tier_2_case_mystery/
    tier_3_court_power/
    tier_4_longform_xianxia/
  downloads/
  processed/
    pattern_cards/
    trope_cards/
    arc_cards/
    foreshadow_cards/
    anti_cliche_cards/
    worldbuilding_cards/
    bureaucracy_cards/
    map_cards/
    faction_cards/
    mechanism_profiles/
  indexes/
  logs/

.local/epic_novel_exports/
.local/epic_novel_runs/

External downloader paths:

SONOVEL_HOME=C:\Users\15050\Desktop\SoNovel
SONOVEL_DOWNLOAD_PATH=C:\Users\15050\Desktop\SoNovel\downloads

Update .gitignore:

.local/
novel_ref_corpus/
reference_corpus/
*.epub
*.txt
*.pdf

==================================================
4. Reference Booklist Layer
==================================================

The user provides a 100-book reference list for ancient transmigration mystery/xianxia/multi-heroine novels.

The system must ingest this list as a seed reference plan.

Supported seed paths:

.local/novel_ref_corpus/booklist_seed.md
.local/novel_ref_corpus/booklist.csv
.local/novel_ref_corpus/booklist.json
docs/epic_novel/reference_booklist_seed.md
docs/epic_novel/reference_booklist.csv
docs/epic_novel/reference_booklist.json

The seed list should be parsed and normalized into structured booklist.csv/json.

Booklist fields:

- id
- title
- author nullable
- tier: 1 | 2 | 3 | 4
- category:
  core_similarity | case_mystery | court_power | longform_xianxia | bureaucracy_reference | metadata_only
- genre
- sourceType:
  sonovel_search | local_file | metadata_only | manual
- sourcePath nullable
- sourceUrl nullable
- corpusRole:
  plot_structure | pleasure | character_arc | heroine_arc | villain_arc | foreshadow | bureaucracy | worldbuilding | power_system | anti_cliche | longform_pacing
- priority: 1-5
- tags
- notes
- downloadStatus:
  pending / searching / found / downloading / downloaded / imported / failed / skipped / manual_required
- ingestAllowed boolean
- needsVerification boolean

Booklist normalization:
The system must read the raw 100-book markdown list and normalize it into structured ReferenceBooklistItem records.

For each item, infer:
- tier
- category
- corpusRole
- priority
- likelyUse
- confidence
- needsVerification boolean

Classification rules:

Tier 1: Core similarity layer.
Use for:
- official/institutional protagonist setup
- case-driven plot
- court + cultivation + mystery fusion
- humorous male-frequency rhythm
- low-status-to-high-system climb
- heroine independent arcs
- small case to big conspiracy
- high-density pleasure structure

Tier 2: Case mystery / xianxia investigation layer.
Use for:
- unit case design
- forensic/fantasy crime mechanism
- monster/ghost mystery
- evidence-chain structure
- clue design
- partial reveal
- foreshadow echo/payoff
- case-to-worldbuilding transition

Tier 3: Court power / statecraft layer.
Use for:
- bureaucracy
- factions
- imperial power
- court conflict
- institution design
- statecraft
- national resources
- military/censorate/law/finance systems
- faction escalation

Tier 4: Longform xianxia / xuanhuan / harem layer.
Use for:
- power system
- cultivation progression
- longform pacing
- heroine relation structure
- long arc expansion
- sect/world escalation
- anti-cliche rules
- 300万字容量设计

The provided list must be normalized, not blindly trusted.
Some titles may be incomplete, duplicates, unverifiable, fanfiction, or metadata-only. Mark them needsVerification=true but still keep metadata.

No single title should dominate the corpus.
The engine must use the entire corpus to infer ReferenceMechanismProfile.

==================================================
5. External Downloader Adapter Layer
==================================================

Do not implement a new crawling engine from scratch.

Implement a pluggable ExternalDownloaderAdapter interface:

ExternalDownloaderAdapter:
- providerName
- detectInstallation()
- getConfig()
- searchBook(title, author?)
- downloadBook(bookId or searchResult)
- listDownloadedFiles()
- importDownloadedFile(filePath)
- getLogs()
- validateOutput()

Implement SoNovelAdapter as the first provider.

SoNovel local tool path:

Default:
C:\Users\15050\Desktop\SoNovel

Config:
REFERENCE_DOWNLOADER_PROVIDER=sonovel
SONOVEL_HOME=C:\Users\15050\Desktop\SoNovel
SONOVEL_DOWNLOAD_PATH=C:\Users\15050\Desktop\SoNovel\downloads
SONOVEL_OUTPUT_FORMAT=epub

SoNovel directory may include:
- app jar
- sonovel executable
- config
- rules
- downloads
- logs
- readme
- runtime

Codex must inspect the local SoNovel directory structure when available and infer the safest way to invoke it.

Do not assume a fixed CLI command if unknown.
Instead:
1. inspect readme/readme.txt/config/config.ini if present
2. inspect executable behavior only if safe
3. inspect logs/downloads structure
4. implement adapter with safe wrappers
5. expose configuration to user if invocation requires manual confirmation

SoNovelAdapter behavior:

1. detectInstallation()
- Check SONOVEL_HOME.
- Confirm app jar / sonovel executable / config / downloads exist.
- Return detected paths.

2. searchBook(title, author?)
- Use SoNovel search capability if CLI/API is discoverable.
- If no CLI search is discoverable, create pending manual command suggestion and record in ReferenceDownloadTask.
- Search must prioritize exact title + author.

3. downloadBook(searchResult)
- Use SoNovel batch/single download if discoverable.
- Output format should be epub by default.
- Save downloaded file into SONOVEL_DOWNLOAD_PATH.
- Also copy/import into .local/novel_ref_corpus/raw/{tier_folder}/.

4. listDownloadedFiles()
- Scan SONOVEL_DOWNLOAD_PATH for epub/txt files.
- Match files to ReferenceBooklistItem by title/author fuzzy match.

5. importDownloadedFile(filePath)
- Copy file to .local/novel_ref_corpus/raw/.
- Compute fileHash.
- Create ReferenceBook record.
- Mark downloadStatus=imported.

6. validateOutput()
- Check file exists.
- Check non-empty.
- Check extension supported.
- Check title can be inferred.
- Check duplicate hash.

If SoNovel cannot be invoked programmatically:
- still implement import workflow from SONOVEL_DOWNLOAD_PATH
- allow user to manually download with SoNovel
- then system auto-detects files and imports them

The engine must not directly generate with SoNovel-downloaded raw text.
Raw text is only used for ReferencePatternCard extraction.

==================================================
6. Reference Download Task Layer
==================================================

Add ReferenceDownloadTask:

- id
- booklistItemId
- provider: sonovel | local_file | manual
- title
- author nullable
- query
- status:
  pending / searching / found / not_found / downloading / downloaded / imported / failed / manual_required
- searchResults JSON
- selectedResult JSON nullable
- localDownloadPath nullable
- importedRawPath nullable
- fileHash nullable
- error nullable
- createdAt
- updatedAt

Download workflow:

1. parse 100-book seed list
2. normalize into ReferenceBooklistItem
3. create ReferenceDownloadTask for each item
4. for high-priority items first:
   - use SoNovelAdapter.searchBook(title, author)
   - save searchResults
   - select best exact match when confidence high
   - download with SoNovel when safe
   - otherwise mark manual_required
5. scan SONOVEL_DOWNLOAD_PATH
6. import downloaded epub/txt
7. create ReferenceBook records
8. ingest raw text into ReferencePatternCards

Matching rules:
- prefer exact title
- then exact title + author
- avoid downloading wrong book if confidence low
- if multiple ambiguous results, mark manual_required

Batch behavior:
- support batch search
- support batch download
- support resume
- support skip downloaded
- support retry failed
- log all download/import statuses

==================================================
7. Reference Raw Text Import & Parsing
==================================================

Supported formats:
- txt
- epub
- md
- html if already downloaded
- json if structured

Implement parsers:
- parseTxtBook
- parseEpubBook
- parseHtmlBook if needed
- normalizeChapters
- splitByChapter
- cleanText
- computeHash

Reference ingestion pipeline:

raw reference book
→ parse file
→ split by chapter/section
→ summarize section
→ extract abstract structure
→ generate pattern cards
→ deduplicate cards
→ score cards
→ index cards
→ build ReferenceMechanismProfile
→ generation retrieves only abstract cards/profile

Allowed card categories:
- pleasure
- plot_structure
- character_arc
- heroine_arc
- villain_arc
- foreshadow
- volume_structure
- power_system
- bureaucracy
- faction
- map
- worldbuilding
- anti_cliche
- longform_pacing

Do not use raw source chunks for chapter generation.
Do not tell the model to imitate source books.
Use cards as abstract mechanisms only.

==================================================
8. Reference Mechanism Profile Layer
==================================================

Add ReferenceMechanismProfile.

The engine must aggregate ReferencePatternCards from the full corpus into a high-level profile.

ReferenceMechanismProfile fields:
- id
- projectId nullable
- corpusId nullable
- profileName
- sourceBookCount
- sourceCardCount
- genre
- tierWeights JSON
- corePleasureMechanisms JSON
- caseStructurePatterns JSON
- courtPowerPatterns JSON
- bureaucracyConflictPatterns JSON
- heroineAgencyPatterns JSON
- characterArcPatterns JSON
- foreshadowPatterns JSON
- powerProgressionPatterns JSON
- longformPacingPatterns JSON
- antiClicheRules JSON
- commonPitfalls JSON
- originalityGuidance JSON
- rawJson
- createdAt
- updatedAt

Build process:
1. collect ReferencePatternCards
2. group by tier/category/corpusRole
3. deduplicate
4. score usefulness
5. generate mechanism profile
6. editor_model reviews
7. save profile

The profile must be used by:
- WorldBible
- WorldAtlas
- PowerSystem
- EnsembleCast
- GlobalSpine
- MasterBible
- VolumeContracts
- ChapterContracts
- QA checkers

The profile is not a prose source.
It is a mechanism source.

==================================================
9. Data Models
==================================================

Use current project database style. Field types can be adapted, but semantics must be preserved.

ReferenceBooklistItem:
- id
- title
- author nullable
- tier
- category
- genre
- sourceType
- sourcePath nullable
- sourceUrl nullable
- corpusRole JSON
- priority
- tags JSON
- notes
- likelyUse JSON
- confidence
- ingestAllowed boolean
- needsVerification boolean
- downloadStatus
- localRawPath nullable
- error nullable
- createdAt
- updatedAt

ReferenceDownloadTask:
- id
- booklistItemId
- provider
- title
- author nullable
- query
- status
- searchResults JSON
- selectedResult JSON nullable
- localDownloadPath nullable
- importedRawPath nullable
- fileHash nullable
- error nullable
- createdAt
- updatedAt

ReferenceBook:
- id
- booklistItemId nullable
- downloadTaskId nullable
- title
- author nullable
- genre
- tier
- category
- sourcePath
- storageType
- fileHash
- language
- status:
  uploaded / parsed / abstracted / indexed / failed
- createdAt
- updatedAt

ReferenceIngestionJob:
- id
- sourcePath
- status
- totalFiles
- processedFiles
- generatedCards
- error
- createdAt
- updatedAt

ReferencePatternCard:
- id
- sourceBookId nullable
- patternKey
- category:
  pleasure / plot_structure / character_arc / heroine_arc / villain_arc /
  foreshadow / volume_structure / power_system / bureaucracy /
  faction / map / worldbuilding / anti_cliche / longform_pacing
- name
- description
- genre
- function
- conditions JSON
- beats JSON
- commonPitfalls JSON
- bestUse JSON
- requiredGlobalLink
- originalityNotes
- tags JSON
- embedding nullable
- rawJson
- createdAt

ReferenceMechanismProfile:
- id
- projectId nullable
- corpusId nullable
- profileName
- sourceBookCount
- sourceCardCount
- genre
- tierWeights JSON
- corePleasureMechanisms JSON
- caseStructurePatterns JSON
- courtPowerPatterns JSON
- bureaucracyConflictPatterns JSON
- heroineAgencyPatterns JSON
- characterArcPatterns JSON
- foreshadowPatterns JSON
- powerProgressionPatterns JSON
- longformPacingPatterns JSON
- antiClicheRules JSON
- commonPitfalls JSON
- originalityGuidance JSON
- rawJson
- createdAt
- updatedAt

EpicNovelProject:
- id
- title
- genre
- premise
- targetWords default 3000000
- targetVolumes default 12
- targetChapters default 700
- style
- status
- currentVolume
- currentChapter
- totalWords
- generationMode
- editorModel
- authorModel
- cheapModel
- referenceProfileId nullable
- createdAt
- updatedAt

WorldviewCandidate:
- id
- projectId
- candidateNumber
- coreConcept
- powerSource
- worldLaw
- societyStructure
- mainConflict
- protagonistSpecialness
- heroineIntegration
- ensemblePotential
- finalMystery
- scoreJson
- selected boolean
- rawJson

EpicWorldBible:
- id
- projectId
- title
- coreConcept
- worldLaw
- powerSource
- societyStructure
- history
- tabooRules JSON
- styleRules JSON
- forbiddenRules JSON
- finalMystery
- endingDirection
- rawJson

EpicWorldAtlas:
- id
- projectId
- nations JSON
- regions JSON
- institutions JSON
- factions JSON
- tradeRoutes JSON
- warZones JSON
- secretRealms JSON
- historicalTimeline JSON
- rawJson

EpicNation:
- id
- projectId
- name
- inspirationProfile
- dynastyFlavor
- rulingIdeology
- politicalStructure
- capital
- provinces JSON
- militarySystem
- fiscalSystem
- legalSystem
- officialSelectionSystem
- bureaucracySystemId
- relationshipToHero
- secrets JSON
- currentCrisis
- rawJson

EpicBureaucracySystem:
- id
- projectId
- nationName
- baseInspiration: HanLike / TangLike / SongLike / MingLike / QinLike / Mixed / Original
- centralGovernment JSON
- localGovernment JSON
- rankSystem JSON
- officialTitles JSON
- departments JSON
- censorateSystem JSON
- judicialSystem JSON
- militaryAdministration JSON
- financeTaxSystem JSON
- examinationSystem JSON
- intelligenceAgencies JSON
- specialAgencies JSON
- courtFactions JSON
- powerChecks JSON
- corruptionChannels JSON
- storyFunctions JSON
- rawJson

EpicMapRegion:
- id
- projectId
- name
- regionType:
  capital / province / county / borderland / mountain / river /
  sect_land / forbidden_zone / secret_realm
- controllingFaction
- geography
- economy
- resources
- strategicValue
- localPowerStructure
- conflicts
- secrets
- relatedForeshadows JSON
- rawJson

EpicInstitution:
- id
- projectId
- name
- institutionType:
  court / censorate / academy / night_watch / astrology_bureau /
  sect / military / prison / archive / temple / guild / spy_agency
- nationName nullable
- factionName nullable
- publicFunction
- hiddenFunction
- hierarchy JSON
- ranks JSON
- internalFactions JSON
- secrets JSON
- relationshipToHero
- storyUseCases JSON
- rawJson

EpicMasterBible:
- id
- projectId
- title
- status: planning / generating / checking / completed / need_review / failed
- targetWordCount
- actualWordCount
- version
- coverageScore
- qualityScore
- exportMarkdownPath
- exportJsonPath
- exportDocxPath nullable
- createdAt
- updatedAt

EpicBibleSection:
- id
- projectId
- bibleId
- sectionKey
- sectionTitle
- sectionType
- orderIndex
- targetWords
- actualWords
- status: planned / generating / generated / checking / accepted / need_review / failed
- contentMarkdown
- contentJson
- dependencies JSON
- coverageTags JSON
- issues JSON
- version
- embedding nullable
- createdAt
- updatedAt

EpicBibleCoverageItem:
- id
- projectId
- bibleId
- itemKey
- itemName
- category
- required boolean
- status: missing / partial / complete
- relatedSectionKeys JSON
- issue
- suggestedFix
- createdAt
- updatedAt

EpicNamedEntityIndex:
- id
- projectId
- entityType:
  character / nation / faction / institution / region / artifact /
  technique / realm / mystery / foreshadow
- name
- alias JSON
- firstDefinedSection
- firstAppearanceChapter nullable
- importance: S / A / B / C / D
- summary
- embedding nullable
- rawJson

EpicPowerSystem:
- id
- projectId
- name
- source
- realmLevels JSON
- progressionRules JSON
- breakthroughConditions JSON
- costs JSON
- limits JSON
- professions JSON
- resources JSON
- protagonistPath JSON
- heroinePaths JSON
- villainUsage JSON
- finalSystemTwist
- rawJson

EpicPowerLedger:
- id
- projectId
- characterName
- volumeNumber nullable
- chapterNumber nullable
- currentRealm
- abilities JSON
- techniques JSON
- artifacts JSON
- resources JSON
- injuries JSON
- trumpCards JSON
- cannotDefeat JSON
- nextBreakthroughCondition
- rawJson

EpicCharacter:
- id
- projectId
- name
- tier: S / A / B / C / D
- role
- gender
- archetype
- independentGoal
- desire
- fear
- falseBelief
- trueNeed
- coreWound
- faction
- powerPath
- relationshipToHero
- activeVolumes JSON
- screenTimeBudget
- ending
- mustNotDo JSON
- rawJson

EpicCharacterArc:
- id
- projectId
- characterName
- tier
- arcStart
- arcMidpoint
- lowPoint
- keyChoice
- cost
- arcEnd
- volumeArcGrid JSON
- rawJson

EpicRelationshipEdge:
- id
- projectId
- fromCharacter
- toCharacter
- relationType
- surfaceRelation
- hiddenConflict
- interestConflict
- emotionalDebt
- currentTensionLevel
- futureExplosionVolume
- possibleResolution
- rawJson

EpicFaction:
- id
- projectId
- name
- type
- ideology
- leader
- publicGoal
- hiddenGoal
- resources
- relationshipToHero
- enemyFactions JSON
- secrets JSON
- status
- rawJson

EpicGlobalSpine:
- id
- projectId
- surfaceStory
- hiddenTruth
- finalConflict
- heroFinalTransformation
- stages JSON
- finalReveal
- rawJson

EpicVolumeContract:
- id
- projectId
- volumeNumber
- title
- wordBudget
- chapterStart
- chapterEnd
- localMainPlot
- globalSpineFunction
- worldAtlasFunction
- bureaucracyFunction
- mapExpansion
- powerProgression
- heroArcFunction
- heroineArcFunction
- majorCharacters JSON
- corePleasures JSON
- setups JSON
- echoes JSON
- payoffs JSON
- endingExplosion
- status
- rawJson

EpicChapterContract:
- id
- projectId
- volumeNumber
- chapterNumber
- title
- targetWords
- chapterGoal
- plotFunction
- globalSpineFunction
- worldAtlasFunction
- bureaucracyFunction
- localArcFunction
- characterArcDeltas JSON
- relationshipChanges JSON
- powerSystemChange JSON
- institutionUsed JSON
- mapRegionUsed JSON
- foreshadowsToPlant JSON
- foreshadowsToEcho JSON
- foreshadowsToPayoff JSON
- readerQuestionRaised
- readerQuestionAnswered
- corePleasure
- secondaryPleasure
- referencePatternCardsUsed JSON
- mustExecute JSON
- mustNotReveal JSON
- endingHook
- status

EpicForeshadow:
- id
- projectId
- foreshadowKey
- name
- setupChapter
- surfaceMeaning
- hiddenMeaning
- firstEchoChapter
- secondEchoChapter
- payoffChapter
- payoffType
- relatedCharacters JSON
- relatedWorldRules JSON
- globalSpineLink
- status: open / echoed / paid_off / warning
- rawJson

EpicReaderQuestion:
- id
- projectId
- questionKey
- question
- introducedChapter
- currentReaderUnderstanding
- falseAnswers JSON
- partialReveals JSON
- finalRevealChapter
- trueAnswer
- status

EpicPleasureBeat:
- id
- projectId
- volumeNumber nullable
- chapterNumber nullable
- pleasureType
- pleasureGoal
- setup
- payoff
- readerReward
- relatedPatternCards JSON
- status

EpicMystery:
- id
- projectId
- mysteryKey
- surfaceMystery
- trueCause
- introducedChapter
- plannedRevealChapter
- clues JSON
- wrongGuesses JSON
- heroInsightMoment
- publicRevealMoment
- status

EpicChapter:
- id
- projectId
- volumeNumber
- chapterNumber
- title
- draftContent
- repairedContent
- acceptedContent
- summary
- endingHook
- wordCount
- status: planned / drafting / checking / repairing / accepted / need_review / failed
- version
- repairCount
- rawModelOutput
- createdAt
- updatedAt

EpicStateExtract:
- id
- projectId
- chapterNumber
- events JSON
- canonFacts JSON
- characterStateDeltas JSON
- relationshipChanges JSON
- powerChanges JSON
- foreshadowActions JSON
- readerQuestionChanges JSON
- pleasureExecution JSON
- factionChanges JSON
- objectStateChanges JSON
- chapterSummary
- endingHook
- rawJson

EpicCanonFact:
- id
- projectId
- chapterNumber
- fact
- factType
- entities JSON
- source
- confidence
- createdAt

EpicCharacterState:
- id
- projectId
- characterName
- chapterNumber
- currentGoal
- emotion
- knowledge JSON
- secrets JSON
- relationships JSON
- powerState JSON
- arcStage
- currentLocation
- rawJson
- createdAt

EpicObjectState:
- id
- projectId
- objectName
- chapterNumber
- holder
- location
- knownBy JSON
- status
- relatedForeshadow
- rawJson
- createdAt

EpicFactionState:
- id
- projectId
- factionName
- chapterNumber
- currentGoal
- resources JSON
- allies JSON
- enemies JSON
- losses JSON
- secretsExposed JSON
- stanceToHero
- rawJson
- createdAt

EpicInstitutionState:
- id
- projectId
- institutionName
- chapterNumber
- currentLeader
- publicStatus
- hiddenStatus
- activeConflict
- relationshipToHero
- knownSecrets JSON
- rawJson
- createdAt

EpicRetrievalTrace:
- id
- projectId
- taskType
- volumeNumber nullable
- chapterNumber nullable
- sectionKey nullable
- query
- retrievedCardIds JSON
- usedCardIds JSON
- usageSummary
- createdAt

EpicAcceptanceReport:
- id
- projectId
- chapterNumber
- outlineExecutionScore
- characterArcScore
- powerLegalityScore
- foreshadowScore
- pleasureScore
- continuityScore
- antiClicheScore
- overallStatus
- issuesJson JSON
- createdAt

EpicIssue:
- id
- projectId
- issueScope:
  bible / outline / chapter / power / character / pleasure /
  foreshadow / continuity / anti_cliche / world_atlas / storage / reference
- volumeNumber nullable
- chapterNumber nullable
- sectionKey nullable
- severity: low / medium / high / severe
- issueType
- description
- evidence
- suggestedFix
- repairScope JSON
- status: open / fixed / ignored
- createdAt

EpicGenerationLog:
- id
- projectId
- action
- provider
- model
- bibleId nullable
- sectionKey nullable
- volumeNumber nullable
- chapterNumber nullable
- promptTokens
- completionTokens
- estimatedCost
- status
- error
- createdAt

==================================================
10. Auto Retrieval Planner
==================================================

Add AutoRetrievalPlanner.

It must decide automatically:
- whether retrieval is needed
- what to retrieve
- why it is needed
- how retrieved cards should be adapted
- how to avoid copying

Run it before:
- worldview generation
- power system generation
- world atlas generation
- bureaucracy generation
- volume contracts
- chapter contracts
- key plot nodes
- outline repair
- chapter repair
- Bible section generation
- Bible repair
- anti-cliche check

Output:
- shouldRetrieve
- retrievalIntents
- query
- intentType
- whyNeeded
- usageConstraint
- mustNotCopy=true

Retrieved cards become Reference Inspiration Pack.

The planner must use:
- ReferencePatternCards
- ReferenceMechanismProfile
- active story state
- current task type

==================================================
11. Plot Function Contract
==================================================

Any RAG-inspired plot idea must first become a Plot Function Contract.

Fields:
- localFunction
- globalSpineLink
- characterArcDelta
- relationshipChange
- foreshadowAction
- futureDebt
- powerSystemImpact
- readerQuestion
- corePleasure
- whyThisIsNotSideQuest

If it lacks globalSpineLink, characterArcDelta, readerQuestion, or futureDebt, reject it as side quest.

==================================================
12. Worldbuilding Atlas Engine
==================================================

Build Worldbuilding Atlas Engine.

It must design:
- nations
- dynasty flavor
- bureaucracy
- censorate
- military
- law/prison
- finance/tax
- examination/official selection
- local governance
- intelligence agencies
- sects
- demons/other races
- jianghu
- merchant guilds
- salt/iron/grain/transport
- academy/history office/astrology bureau/night watch
- map regions
- secret realms
- state differences
- faction conflicts
- timeline and final mystery

Historical inspiration profiles:
HanLike, TangLike, SongLike, MingLike, QinLike, Mixed, Original.

Must transform historical inspiration into original fantasy institutions.
Do not copy specific novel institutions.

Implement generateWorldAtlas(projectId).

==================================================
13. Mega Bible Compiler
==================================================

Add Master Bible.

Distinguish:
- Master Bible: world, characters, maps, factions, power, foreshadows, style, ending.
- Master Outline: volumes, chapter progression, plot.
- Chapter Contract: per-chapter execution requirements.

Default Bible target words: 100000 Chinese characters.
Support 30000, 100000, 200000, custom.

Do not generate full Bible in one call.
Generate by sections.

Implement:
- generateBibleManifest(projectId)
- generateBibleSection(projectId, sectionKey)
- runBibleCoverageCI(projectId)
- repairBibleCoverage(projectId)
- exportBible(projectId, format)

Bible Manifest must include:
- project overview
- genre promise
- core concept
- world law
- power system
- nation and court
- bureaucracy
- map and geography
- factions
- characters
- heroine independent arcs
- relationship graph
- history timeline
- global spine
- volume contracts
- foreshadow ledger
- reader question ledger
- pleasure curve
- villain plan
- style guide
- forbidden and anti-cliche rules
- ending plan
- glossary

==================================================
14. Genesis Layer
==================================================

Implement:
- runWorldviewTournament(projectId)
- forgePowerSystem(projectId)
- generateWorldAtlas(projectId)
- generateEnsembleCast(projectId)
- generateGlobalSpine(projectId)

Worldview tournament:
- retrieve reference cards
- use ReferenceMechanismProfile
- generate 10-20 candidates
- editor_model judges
- merge top 3
- save EpicWorldBible

Power system:
- original system
- stress test
- update EpicPowerSystem

Ensemble:
- protagonist
- multiple heroines
- core male supports
- villains
- mentors
- factions
- independent arcs and endings

==================================================
15. Planning Layer
==================================================

Implement:
- generateVolumeContracts(projectId)
- generateArcLedgers(projectId)
- runOutlineCI(projectId)
- repairOutline(projectId)

Volume contracts must include:
- localMainPlot
- globalSpineFunction
- worldAtlasFunction
- bureaucracyFunction
- mapExpansion
- powerProgression
- heroArcFunction
- heroineArcFunction
- majorCharacters
- corePleasures
- setups
- echoes
- payoffs
- endingExplosion

Arc ledgers:
- Character Arc Grid
- Power Ledger
- Foreshadow Ledger
- Reader Question Ledger
- Pleasure Curve
- Mystery Ledger
- Institution Conflict Ledger
- Map Progression Ledger

Outline CI checks:
- every volume serves Global Spine
- every volume advances WorldAtlas
- bureaucracy is used, not background only
- map progression is logical
- faction conflict escalates
- power does not break
- A-tier characters do not disappear
- heroines are not appendages
- foreshadows have setup/echo/payoff
- pleasure gaps are detected
- no side quests
- institutions are consistent

==================================================
16. Execution Layer
==================================================

Implement compileChapterContracts(projectId, volumeNumber).

Each chapter contract must include:
- plotFunction
- globalSpineFunction
- worldAtlasFunction
- bureaucracyFunction
- localArcFunction
- characterArcDeltas
- relationshipChanges
- powerSystemChange
- institutionUsed
- mapRegionUsed
- foreshadowsToPlant/Echo/Payoff
- readerQuestionRaised/Answered
- corePleasure
- referencePatternCardsUsed
- mustExecute
- mustNotReveal
- endingHook

Implement buildChapterContextPack(projectId, chapterNumber).

Must read:
- Chapter Contract
- related Master Bible sections
- WorldBible
- WorldAtlas
- current nation/bureaucracy/institution
- current map region
- GlobalSpine
- VolumeContract
- recent 2-3 chapter summaries
- previous chapter endingHook
- latest character states
- latest relationship states
- current power ledger
- active foreshadows
- active reader questions
- current pleasure curve
- active institution/faction state
- ReferencePatternCards
- ReferenceMechanismProfile summary

For chapterNumber > 1:
previousChapter.summary and previousChapter.endingHook must exist, or block generation.

Implement executeChapter(projectId, chapterNumber):

1. ensure WorldBible
2. ensure MasterBible
3. ensure WorldAtlas
4. ensure PowerSystem
5. ensure GlobalSpine
6. ensure VolumeContract
7. ensure ChapterContract
8. run AutoRetrievalPlanner
9. build Reference Inspiration Pack
10. build Chapter Context Pack
11. generate draftContent through existing short-form generator or author_model
12. save draftContent
13. extractChapterState
14. plannedVsActualDiff
15. runChapterAcceptance
16. if high/severe issue: repair once, re-extract, re-check
17. if pass: acceptedContent persists and updates state ledgers
18. else mark need_review

==================================================
17. QA Layer
==================================================

Implement:

- Outline Execution Checker
- Bible Usage Checker
- World Atlas Checker
- Character Arc Checker
- Heroine Agency Checker
- Power Ledger Checker
- Foreshadow Checker
- Pleasure Beat Checker
- Continuity Checker
- Anti-Cliche Checker
- Plot Function Checker
- Storage State Checker
- Reference Safety Checker
- Reference Overfit Checker

Storage State Checker must verify:
- draftContent not in canon
- repairedContent not in canon
- acceptedContent updates state ledgers
- State Extract corresponds to acceptedContent
- RetrievalTrace recorded
- AcceptanceReport recorded
- status transitions are valid

Reference Safety Checker must verify:
- raw text not sent to generation prompts
- usedCards are ReferencePatternCards only
- download/import status is recorded
- source file hash is recorded
- card sourceBookId exists

Reference Overfit Checker must verify:
- output does not copy names, prose, scenes, unique institutions, exact twists, or chapter structures
- output uses abstract mechanisms only

High/severe blocks accepted.

==================================================
18. Prompt Templates
==================================================

Add prompt templates, not hardcoded:

reference_booklist_parse_prompt
reference_booklist_normalize_prompt
sonovel_search_result_match_prompt
reference_ingest_prompt
reference_pattern_extract_prompt
reference_mechanism_profile_prompt
reference_mechanism_profile_review_prompt
auto_retrieval_planner_prompt
reference_inspiration_pack_prompt
plot_function_contract_prompt
worldview_tournament_prompt
worldview_judge_prompt
world_atlas_prompt
bureaucracy_design_prompt
power_system_forge_prompt
power_system_stress_test_prompt
ensemble_cast_prompt
global_spine_prompt
bible_manifest_prompt
bible_section_prompt
bible_section_check_prompt
bible_coverage_ci_prompt
bible_repair_prompt
volume_contract_prompt
character_arc_grid_prompt
foreshadow_ledger_prompt
reader_question_ledger_prompt
pleasure_curve_prompt
outline_ci_prompt
outline_repair_prompt
chapter_contract_prompt
chapter_context_pack_prompt
chapter_write_prompt
state_extract_prompt
planned_vs_actual_diff_prompt
chapter_acceptance_prompt
repair_chapter_prompt

==================================================
19. API Routes
==================================================

Implement equivalent project-style routes:

POST /api/epic-novel/projects

POST /api/epic-novel/reference-booklist/import
POST /api/epic-novel/reference-booklist/normalize

POST /api/epic-novel/reference-downloader/detect
POST /api/epic-novel/reference-downloader/search
POST /api/epic-novel/reference-downloader/download
POST /api/epic-novel/reference-downloader/batch-download
POST /api/epic-novel/reference-downloader/scan-downloads
POST /api/epic-novel/reference-downloader/import-downloaded
GET  /api/epic-novel/reference-download-tasks
GET  /api/epic-novel/reference-download-tasks/:id

POST /api/epic-novel/reference-books/import
POST /api/epic-novel/reference-books/ingest
POST /api/epic-novel/reference-patterns/search
POST /api/epic-novel/reference-mechanism-profile/build
GET  /api/epic-novel/reference-mechanism-profile

POST /api/epic-novel/projects/:id/worldview-tournament
POST /api/epic-novel/projects/:id/world-atlas
POST /api/epic-novel/projects/:id/power-system
POST /api/epic-novel/projects/:id/ensemble-cast
POST /api/epic-novel/projects/:id/global-spine

POST /api/epic-novel/projects/:id/bible/manifest
POST /api/epic-novel/projects/:id/bible/sections/:sectionKey/generate
POST /api/epic-novel/projects/:id/bible/coverage-ci
POST /api/epic-novel/projects/:id/bible/repair
GET  /api/epic-novel/projects/:id/bible/export?format=markdown
GET  /api/epic-novel/projects/:id/bible/export?format=json
GET  /api/epic-novel/projects/:id/bible/export?format=docx

POST /api/epic-novel/projects/:id/volume-contracts
POST /api/epic-novel/projects/:id/arc-ledgers
POST /api/epic-novel/projects/:id/outline-ci
POST /api/epic-novel/projects/:id/outline-repair

POST /api/epic-novel/projects/:id/volumes/:volumeNumber/compile-chapters
GET  /api/epic-novel/projects/:id/chapters/:chapterNumber/context-pack
POST /api/epic-novel/projects/:id/chapters/:chapterNumber/execute
POST /api/epic-novel/projects/:id/chapters/:chapterNumber/check
POST /api/epic-novel/projects/:id/chapters/:chapterNumber/repair

POST /api/epic-novel/projects/:id/volumes/:volumeNumber/generate
POST /api/epic-novel/projects/:id/generate-batch

GET /api/epic-novel/projects/:id/dashboard
GET /api/epic-novel/projects/:id/export?format=markdown

==================================================
20. Frontend Workspace
==================================================

Add entry:
史诗长篇 / 300万字网文

Form:
- title
- premise
- genre default: 古代穿越·悬疑探案·朝堂仙侠·多女主
- core pleasure preferences
- target words
- target volumes
- Bible target words
- reference booklist path
- SoNovel path
- SoNovel download path
- reference raw path
- generation mode
- forbidden rules

Tabs:
- 项目总览
- 参考书单
- SoNovel下载器
- 下载任务
- 参考书库
- 参考模式卡
- 参考机制画像
- 世界观候选
- 最终世界观圣经
- Master Bible / 全书圣经
- 世界图谱
- 国家设定
- 官僚体系
- 地图区域
- 机构势力
- 战力体系
- 群像人物
- 角色弧光表
- 关系图谱
- 全书大主线
- 分卷合同
- 章节合同
- 伏笔账本
- 读者疑问账本
- 爽点曲线
- 战力账本
- 正文
- Issues
- 圣经 Coverage CI
- 大纲 CI
- 质量仪表盘
- 生成日志
- 导出

Reference Library page must show:
- normalized 100-book list
- tier/category/corpusRole
- downloadStatus
- SoNovel search results
- selected result
- imported raw path
- generated ReferencePatternCards
- ReferenceMechanismProfile

Dashboard:
- current words
- completed chapters
- completed volumes
- Bible coverageScore
- outline execution rate
- WorldAtlas usage rate
- bureaucracy consistency issues
- power violations
- character arc gaps
- A-tier disappearance warnings
- heroine appendage risk
- unpaid foreshadows
- pleasure gap warnings
- side quest warnings
- continuity issues
- anti-cliche issues
- storage state errors
- reference safety warnings
- reference overfit warnings
- download/import status
- generated pattern card count
- repair count
- cost estimate

==================================================
21. Batch Generation
==================================================

Strict serial flow:

Chapter Contract
→ Auto Retrieval Planner
→ Reference Inspiration Pack
→ Context Pack
→ Draft
→ State Extract
→ Planned vs Actual Diff
→ Acceptance Gate
→ Repair if needed
→ Re-extract
→ Re-check
→ Accepted
→ Persist State
→ Next Chapter

After each volume:
- editor_model volume review
- update future risks
- propose Outline Patch if needed
- continue only after CI passes

==================================================
22. Test Project
==================================================

Use:

Title:
《无名天书》

Type:
古代穿越·朝堂仙侠悬疑

Core idea:
现代内容行业从业者沈知微穿越到一个古代王朝，成为夜巡司最底层的小吏。这个世界的修行不是吸收灵气，而是凝聚“名箓”：一个人被世人如何记住、传颂、误解、污名化，就会获得怎样的力量。朝廷控制史书，仙门控制传说，妖魔控制恐惧，读书人控制名声。主角一开始只想活下去，却在一桩更夫失魂案里发现，所谓修行体系背后藏着一个贯穿王朝三百年的旧局。

Core pleasures:
智力压制、小案牵大局、草蛇灰线、创新战力体系、多女主独立线、群像、低位破高位局。

Forbidden:
不要系统流，不要无脑开挂，不要主角突然变强，不要反派降智，不要女主无理由倒贴，不要大段设定说明，不要每章只解释世界观，不要临时副本，不要战力崩，不要主角淡淡一笑众人震惊式假爽。

Milestone:
- parse/normalize the provided 100-book list
- classify all books into four tiers and corpus roles
- detect SoNovel installation
- create ReferenceDownloadTasks
- search at least Tier 1 books with SoNovel
- download/import available books through SoNovel adapter when possible
- scan SoNovel downloads directory
- import epub/txt into .local/novel_ref_corpus/raw/
- generate ReferencePatternCards from imported raw text
- build ReferenceMechanismProfile from all available cards
- AutoRetrievalPlanner retrieves pattern cards/profile
- generate WorldBible
- generate WorldAtlas
- generate BureaucracySystem
- generate PowerSystem
- generate Ensemble Cast
- generate Global Spine
- generate Master Bible manifest
- generate at least 10 Bible sections
- run Bible Coverage CI
- generate 3 volume contracts
- compile first 10 chapter contracts
- execute first 3 chapters
- verify chapter 2/3 read previous state, Bible sections, WorldAtlas, bureaucracy, and ReferenceMechanismProfile
- verify draft/repaired do not write canon
- verify acceptedContent writes state ledgers
- verify RetrievalTrace and AcceptanceReport are recorded
- export Bible markdown/json
- export novel markdown

==================================================
23. Acceptance Criteria
==================================================

Must satisfy:

1. Existing short-form generation still works.
2. EpicNovelProject can be created.
3. Reference local storage can be configured.
4. The 100-book markdown seed list can be parsed.
5. The list can be normalized into four tiers.
6. No single title is hardcoded as anchor.
7. ReferenceDownloadTasks can be created.
8. SoNovel installation can be detected or manual-required status is shown.
9. SoNovel downloads directory can be scanned.
10. Downloaded epub/txt can be imported.
11. ReferencePatternCards can be generated from imported raw text.
12. ReferenceMechanismProfile can be built from the full corpus.
13. Raw reference text is never sent to generation prompts.
14. AutoRetrievalPlanner retrieves pattern cards/profile.
15. WorldBible can be generated.
16. WorldAtlas can be generated.
17. BureaucracySystem can be generated.
18. PowerSystem can be generated.
19. Ensemble Cast can be generated.
20. Global Spine can be generated.
21. EpicMasterBible can be created.
22. Bible Manifest can be generated.
23. Bible sections can be generated.
24. Bible Coverage CI works.
25. Bible export markdown/json works.
26. Volume Contracts can be generated.
27. Chapter Contracts can be compiled.
28. Chapter Context Pack can be viewed.
29. First 3 chapters can be executed.
30. Chapter 2/3 read previous state, Bible sections, WorldAtlas, bureaucracy, and ReferenceMechanismProfile.
31. draftContent and repairedContent never write to canon.
32. acceptedContent writes to state ledgers.
33. StateExtract corresponds to acceptedContent.
34. Planned-vs-Actual Diff works.
35. Acceptance Gate works.
36. RetrievalTrace is recorded.
37. AcceptanceReport is recorded.
38. Object / Character / Faction / Institution states are readable by next chapter.
39. Chapters generate strictly serially.
40. Frontend workspace exists.
41. Dashboard exists.
42. Bible can be exported.
43. Novel markdown can be exported.
44. All LLM calls have logs and cost records.

==================================================
24. Implementation Order
==================================================

Implement in order:

1. Inspect repo.
2. Write implementation plan.
3. Extend LLM Router.
4. Add data models and migrations.
5. Add prompt templates.
6. Implement Storage & State Layer.
7. Implement reference seed list parser.
8. Implement booklist normalizer.
9. Implement ExternalDownloaderAdapter interface.
10. Implement SoNovelAdapter.
11. Implement ReferenceDownloadTask workflow.
12. Implement SoNovel downloads scanner.
13. Implement raw epub/txt import.
14. Implement Reference RAG ingestion and PatternCard search.
15. Implement ReferenceMechanismProfile builder.
16. Implement AutoRetrievalPlanner.
17. Implement WorldAtlas.
18. Implement MegaBible.
19. Implement Genesis.
20. Implement Planning.
21. Implement Execution.
22. Implement QA.
23. Implement API routes.
24. Implement frontend workspace.
25. Implement dashboard.
26. Implement export.
27. Write basic tests.
28. Run test project milestone.
29. Update IMPLEMENTATION_STATUS.md.
30. Summarize changed files, commands, test results, remaining TODOs.

If full implementation is too large:
Implement as much as possible, prioritizing:
1. data models
2. model router
3. storage/state layer
4. seed list parser and normalizer
5. SoNovelAdapter
6. download scanner/importer
7. ReferencePatternCard extraction/search
8. ReferenceMechanismProfile builder
9. AutoRetrievalPlanner
10. WorldAtlas
11. MegaBible
12. Execution + Acceptance Gate
13. API
14. frontend shell

Do not stop at documentation only.
Build runnable code as much as possible.