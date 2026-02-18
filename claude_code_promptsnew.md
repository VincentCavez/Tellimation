# Tellimations v2: Claude Code Prompts (in order)

## Step 0: Project Setup

```
Lis le CLAUDE.md. Initialise le projet: crée la structure de dossiers décrite dans "Project Structure", un requirements.txt avec les dépendances (pydantic, google-genai, fastapi, uvicorn, websockets, pytest, numpy), et config/skill_framework.yaml avec un squelette du SKILL framework contenant des objectifs de test (descriptive_adjectives, spatial_prepositions, temporal_sequences, quantity, action_verbs). Ne code rien d'autre encore.
```

## Step 1: Data Models

```
Lis le CLAUDE.md. Implémente les data models dans src/models/ avec Pydantic.

1. scene.py: Entity (id, type, properties: dict, position: {x, y, spatial_ref}, emotion, carried_over: bool), Relation (entity_a, entity_b, type, preposition), Action (entity_id, verb, tense, manner), SceneManifest (scene_id, entities: list, relations: list, actions: list).

2. neg.py: NarrativeTarget (id, entity_id, components avec identity/descriptors/spatial/action/temporal, priority, tolerance), ErrorExclusion (entity_id, excluded: list[str], reason: str), NEG (targets: list, error_exclusions: list, min_coverage: float, skill_coverage_check: str).

3. story_state.py: ActiveEntity (type, sprite_code: str, first_appeared: str, last_position: dict), StoryState (session_id, participant_id: str, skill_objectives: list[str], scenes: list[dict], active_entities: dict[str, ActiveEntity]). Ajoute des méthodes: add_scene(), get_entity_sprite(entity_id), carry_over_entities(new_manifest) qui retourne la liste des entités à reprendre et celles à générer.

4. student_profile.py: StudentProfile (error_counts: dict[str, int], error_trend: dict[str, str], difficult_entities: list[str], strong_areas: list[str], scenes_completed: int, corrections_after_animation: int, total_utterances: int). Ajoute des méthodes: record_errors(discrepancies: list), update_trends(), get_weak_areas() -> list[str], to_prompt_context() -> str qui formate le profil pour injection dans un prompt LLM.

5. animation_cache.py: CachedAnimation (code: str, duration_ms: int, generated_for: str), AnimationCache (cache: dict[str, dict[str, CachedAnimation]]). Méthodes: lookup(entity_id, error_type) -> CachedAnimation | None, store(entity_id, error_type, animation), has(entity_id, error_type) -> bool. Le lookup doit supporter le prefix matching: si on cherche "rabbit_01" et qu'il y a un cache pour "rabbit_01.body", ça match.

Écris des tests dans tests/test_models.py qui vérifient la sérialisation JSON, les méthodes de StoryState et StudentProfile, et le prefix matching de AnimationCache.
```

## Step 2: Pixel Engine (JavaScript, client-side)

```
Lis le CLAUDE.md, section "Pixel Art Engine". Implémente src/ui/static/engine.js.

Ce fichier contient le moteur de rendu pixel art côté client. Il doit exporter:

1. La classe PixelBuffer:
   - Constructeur(width, height) qui crée le buffer (array d'objets {r,g,b,e})
   - Méthodes primitives: px(), rect(), circ(), ellip(), tri(), line(), thickLine(), arc() -- exactement l'API documentée dans CLAUDE.md
   - getPixel(x, y) -> {r, g, b, e}
   - getPixelsForPrefix(prefix) -> array d'indices
   - getEntityBounds(prefix) -> {x1, y1, x2, y2}
   - snapshot() qui sauvegarde _r, _g, _b pour chaque pixel (utilisé avant les animations)
   - restore() qui remet r=_r, g=_g, b=_b

2. La classe Renderer:
   - Constructeur(canvas, pixelBuffer, scale=3)
   - render() qui dessine le buffer sur le canvas via ImageData, avec image-rendering: pixelated
   - drawBackground(type) qui dessine ciel + sol (paramétrable: forest, beach, cave, city...)

3. La classe EntityRegistry:
   - Construit automatiquement l'arbre hiérarchique depuis les entity IDs présents dans le buffer
   - getTree() -> objet imbriqué
   - getAllEntities() -> liste de tous les IDs uniques
   - getChildren(prefix) -> IDs directs sous ce prefix

4. Fonction executeSpriteCode(code, pixelBuffer):
   - Crée un scope avec les primitives du buffer
   - Exécute le code JavaScript généré par le LLM via new Function()
   - Gère les erreurs d'exécution proprement

Taille du canvas: 280x180 pixels, scale 3x.

Écris un fichier test src/ui/static/test_engine.html qui crée un buffer, dessine quelques formes avec des entity IDs hiérarchiques, affiche le rendu, et montre l'arbre d'entités dans la console.
```

## Step 3: Animation Runner (JavaScript, client-side)

```
Lis le CLAUDE.md, section "Animation System". Implémente src/ui/static/animations.js.

Ce fichier gère l'exécution des animations sur le pixel buffer.

1. Classe AnimationRunner:
   - Constructeur(pixelBuffer, renderer)
   - play(animationCode: string, durationMs: number) -> Promise qui resolve quand l'animation est finie
   - Le code d'animation est une string qui sera compilée en Function(buf, PW, PH, t) où t va de 0 à 1
   - Avant de jouer: appeler pixelBuffer.snapshot() pour sauvegarder l'état original
   - Pendant l'animation: requestAnimationFrame loop qui calcule t, appelle la fonction, puis renderer.render()
   - Après: appeler pixelBuffer.restore() et renderer.render()
   - stop() pour interrompre une animation en cours
   - isPlaying: bool

2. Bibliothèque d'animations de base (pas générées par LLM, pour fallback):
   Implémente en dur les animations les plus simples qui servent de fallback si le LLM rate:
   - colorPop(entityPrefix) -> code string
   - shake(entityPrefix) -> code string
   - pulse(entityPrefix) -> code string
   - isolate(entityPrefix) -> code string
   - bounce(entityPrefix) -> code string
   Chacune retourne une string de code d'animation compatible avec AnimationRunner.play()

Écris un test_animations.html qui charge un buffer avec une scène simple (2-3 entités), et des boutons pour déclencher chaque animation de base. Vérifie que snapshot/restore fonctionne (la scène revient à l'état initial après chaque animation).
```

## Step 4: Scene Generator (Python, server-side)

```
Lis le CLAUDE.md, sections "Scene Generation" et "Hierarchical Entity IDs". Implémente src/generation/scene_generator.py et src/generation/prompts/scene_prompt.py.

scene_prompt.py contient le system prompt pour la génération de scène. Ce prompt doit:
- Documenter le format JSON de sortie attendu (manifest + NEG + sprite_code)
- Documenter l'API des primitives pour le sprite code (px, circ, ellip, rect, tri, line, thickLine, arc)
- Expliquer les règles de nommage hiérarchique des entity IDs (root.part.subpart)
- Demander au moins 8 sub-entities par entité dans le sprite code
- Inclure les règles de vérification NEG (le LLM doit checker que les SKILL objectives sont couverts et enrichir la scène si non)
- Demander le sprite code UNIQUEMENT pour les nouvelles entités (carried_over = false)
- Spécifier la taille du canvas (280x180), le sol à y~100, le sprite centré

scene_generator.py:
- Fonction generate_scene(story_state: StoryState | None, student_profile: StudentProfile | None, skill_objectives: list[str], seed_index: int = 0) -> dict
- Si story_state est None (première génération, page de sélection): le prompt demande une scène d'ouverture aléatoire avec un personnage, un setting, et une accroche narrative. seed_index (1, 2, 3...) est injecté pour varier les résultats.
- Si story_state est fourni (scènes suivantes): le prompt inclut le contexte complet (story_state, student_profile)
- Appelle Gemini 3 Flash (thinking_level: medium)
- Parse la réponse JSON
- Valide que le manifest est un SceneManifest valide, que le NEG est un NEG valide
- Si story_state fourni: met à jour story_state avec la nouvelle scène et les nouveaux sprites
- Retourne le dict complet (manifest + neg + sprite_code + carried_over_entities + branch_summary)

Écris un test qui:
1. Appelle generate_scene SANS story_state (initial) et vérifie qu'une scène aléatoire valide est retournée
2. Appelle generate_scene AVEC un story_state et vérifie la continuité (carried_over_entities non vide)
```

## Step 5: Branch Generator

```
Lis le CLAUDE.md, section "Branch Generation". Implémente src/generation/branch_generator.py.

Fonction principale: generate_branches(story_state: StoryState | None, student_profile: StudentProfile | None, skill_objectives: list[str], n_branches: int = 3) -> list[dict]

Cette fonction est utilisée dans deux contextes:
1. **Page de sélection initiale** (story_state=None): génère n_branches scènes d'ouverture aléatoires
2. **Entre les scènes** (story_state fourni): génère n_branches suites possibles

La fonction:
1. Lance n_branches appels parallèles à generate_scene (utilise asyncio.gather ou concurrent.futures)
2. Chaque appel reçoit un seed_index différent (1, 2, 3...) pour varier
3. Chaque branche inclut un "branch_summary" (1-2 phrases pour l'enfant) et "preview_entities"
4. Retourne la liste des résultats complets

Fonction secondaire: generate_one_more(existing_branches: list[dict], story_state: StoryState | None, student_profile: StudentProfile | None, skill_objectives: list[str]) -> dict
- Génère UNE seule branche supplémentaire avec un seed_index = len(existing_branches) + 1
- Utilisée par le bouton "I want to see one more" sur la page de sélection

Important: le prompt de branchement doit:
- Donner un numéro de branche (1/2/3) pour que le LLM varie
- Demander des directions narratives contrastées (ex: exploration, conflit, rencontre)
- Injecter le student_profile pour que chaque branche cible des faiblesses différentes de l'enfant

Écris un test qui génère 3 branches et vérifie que les branch_summary sont différents et que chaque branche a un manifest valide.
```

## Step 6: Animation Generator

```
Lis le CLAUDE.md, sections "Animation System" et "Animation Grammar". Implémente src/generation/animation_generator.py et src/generation/prompts/animation_prompt.py.

animation_prompt.py contient le system prompt pour la génération de code d'animation. Ce prompt doit:
- Documenter le format de la fonction d'animation: function animate(buf, PW, PH, t) où buf est le pixel buffer, t va de 0.0 à 1.0
- Expliquer que buf[i] a les champs: r, g, b, e (entity id string), _r, _g, _b (couleurs originales sauvegardées)
- Documenter les helpers disponibles: buf[i].e.startsWith(prefix) pour le prefix matching
- Inclure la description complète de chaque type d'animation de la grammaire (copier depuis CLAUDE.md)
- Demander au LLM de choisir l'animation la plus sémantiquement pertinente pour l'erreur
- Demander une durée en ms
- Donner des exemples concrets de code pour 2-3 animations (color_pop, shake, settle)

animation_generator.py:
- Fonction generate_animation(error_type: str, entity_id: str, sub_entity: str, entity_bounds: dict, scene_context: dict, animation_cache: AnimationCache) -> CachedAnimation
- D'abord: vérifier le cache. Si trouvé, retourner immédiatement.
- Sinon: appeler Gemini 3 Flash (thinking_level: medium) avec le prompt + contexte
- Parser le code et la durée
- Stocker dans le cache
- Retourner le CachedAnimation

Écris un test qui génère une animation pour une erreur property_color sur un entity "rabbit_01.body" et vérifie que le code retourné est une string non-vide, que le cache est mis à jour, et qu'un deuxième appel identique retourne le cache sans appel API.
```

## Step 7: Error Exclusion Rules

```
Lis le CLAUDE.md, section "Error Exclusion Rules". Implémente src/narration/error_exclusions.py.

Ce module est purement algorithmique, pas de LLM.

1. Enum ErrorType avec toutes les valeurs: SPATIAL, PROPERTY_COLOR, PROPERTY_SIZE, PROPERTY_WEIGHT, PROPERTY_TEMPERATURE, PROPERTY_STATE, TEMPORAL, IDENTITY, QUANTITY, ACTION, RELATIONAL, EXISTENCE, MANNER, REDUNDANCY, OMISSION.

2. Fonction compute_exclusions(entity: Entity, manifest: SceneManifest) -> list[ErrorExclusion]:
   - Unique dans la scène -> exclure QUANTITY
   - Pas de couleur dans properties -> exclure PROPERTY_COLOR
   - Pas d'action associée -> exclure MANNER, ACTION
   - Pas de poids dans properties -> exclure PROPERTY_WEIGHT
   - Pas de température -> exclure PROPERTY_TEMPERATURE
   - Pas de relation spatiale -> exclure SPATIAL
   - Type "background" ou "decoration" -> exclure IDENTITY
   Chaque exclusion a une raison string.

3. Fonction is_excluded(entity_id: str, error_type: str, exclusions: list[ErrorExclusion]) -> bool

4. Fonction filter_discrepancies(discrepancies: list, exclusions: list[ErrorExclusion]) -> list qui retire les discrepancies impossibles.

Écris des tests exhaustifs pour chaque règle d'exclusion. Construis des Entity et SceneManifest de test à la main.
```

## Step 8: Transcription + Discrepancy Detection

```
Lis le CLAUDE.md, section "Narration Loop". Implémente src/narration/transcription.py et src/generation/prompts/transcription_prompt.py.

transcription_prompt.py: system prompt pour Gemini 3 Flash en mode multimodal (audio + texte). Le prompt doit:
- Expliquer le rôle (transcrire la parole d'un enfant 7-11 ans et détecter les erreurs par rapport au NEG)
- Inclure la taxonomie complète d'erreurs avec des exemples concrets pour chaque type
- Demander un JSON structuré en sortie (transcription, discrepancies, scene_progress, satisfied_targets, updated_history, profile_updates)
- Préciser que le NEG est fourni comme "attente" et que les divergences doivent être typées précisément
- Demander une severity entre 0 et 1 pour chaque discrepancy

transcription.py:
- Pydantic models: Discrepancy (type, entity_id, sub_entity, details, severity), TranscriptionResult (transcription, discrepancies, scene_progress, satisfied_targets, updated_history, profile_updates)
- Fonction transcribe_and_detect(audio_bytes: bytes, neg: NEG, narration_history: list[str], student_profile: StudentProfile) -> TranscriptionResult
- Appelle Gemini 3 Flash (thinking_level: low) avec audio + NEG en JSON + history + profile
- Parse la réponse JSON en TranscriptionResult
- Filtre les discrepancies via les error_exclusions du NEG

Écris un test qui mocke l'appel Gemini et vérifie que le parsing fonctionne et que les exclusions filtrent correctement.
```

## Step 9: Dispatcher

```
Lis le CLAUDE.md. Implémente src/narration/dispatcher.py.

Le dispatcher est le pont entre la détection d'erreurs et le système d'animation.

Fonction principale: dispatch(discrepancies: list[Discrepancy], animation_cache: AnimationCache, entity_bounds: dict, scene_context: dict) -> list[AnimationCommand]

Où AnimationCommand = {entity_id, sub_entity, error_type, cached: bool, animation: CachedAnimation | None}

Logique:
1. Trier les discrepancies par severity décroissante
2. Garder max 2 (pas surcharger l'enfant)
3. Pour chaque discrepancy retenue:
   a. Chercher dans animation_cache
   b. Si trouvé: AnimationCommand avec cached=True et l'animation
   c. Si pas trouvé: AnimationCommand avec cached=False et animation=None (le narration_loop appellera animation_generator)
4. Retourner la liste

Fonction hesitation: dispatch_hesitation(neg: NEG, satisfied_targets: list[str]) -> AnimationCommand | None
- Trouve le target de plus haute priorité non satisfait
- Retourne une AnimationCommand pour une animation d'omission (sprouting)

Écris des tests couvrant: 1 discrepancy, 3 discrepancies (vérifie max 2), cache hit, cache miss, hesitation.
```

## Step 10: Narration Loop Orchestrator

```
Lis le CLAUDE.md. Implémente src/narration/narration_loop.py.

C'est le coeur du système temps réel. Il orchestre: audio -> transcription -> dispatch -> animation.

Classe NarrationLoop:
- Constructeur(scene_manifest, neg, story_state, student_profile, animation_cache, websocket)
- État: narration_history, satisfied_targets, scene_progress, idle_timer
- Méthode async on_audio_chunk(audio_bytes):
  1. Appeler transcribe_and_detect
  2. Mettre à jour student_profile avec profile_updates
  3. Mettre à jour satisfied_targets et scene_progress
  4. Appeler dispatch pour obtenir les AnimationCommands
  5. Pour chaque AnimationCommand non cachée: appeler animation_generator (async)
  6. Envoyer les animations au client via WebSocket (JSON: {type: "animation", code: "...", duration_ms: 1200, entity_id: "..."})
  7. Retourner le TranscriptionResult
- Méthode on_idle_timeout():
  1. Appeler dispatch_hesitation
  2. Envoyer l'animation d'omission
- Méthode is_scene_complete() -> bool (scene_progress >= neg.min_coverage)
- Propriété get_session_log() pour les analytics

Le WebSocket envoie au client:
- {type: "scene", sprite_code: {...}, carried_over: [...], manifest: {...}} pour charger une scène
- {type: "animation", code: "...", duration_ms: 1200} pour jouer une animation
- {type: "branches", branches: [{summary: "...", preview_sprite_code: {...}}, ...]} pour le choix
- {type: "scene_complete"} quand la scène est finie

Le client envoie au serveur:
- {type: "audio", data: base64_audio}
- {type: "branch_choice", index: 0|1|2}

Écris un test d'intégration qui simule un scénario complet: envoyer 3 chunks audio mockés, vérifier que les animations sont dispatched, vérifier que scene_progress augmente, vérifier la mise à jour du student_profile.
```

## Step 11: Web UI

```
Lis le CLAUDE.md, sections "Pages" et "Session Flow". Implémente src/ui/app.py et les templates.

app.py: serveur FastAPI avec:
- Route GET / qui sert login.html
- Route GET /selection qui sert selection.html (redirige vers / si pas de clé API en session)
- Route GET /story qui sert story.html (redirige vers /selection si pas de scène choisie)
- Route statique /static pour les fichiers JS et CSS
- WebSocket /ws qui gère la session:
  - Le client envoie la clé API Gemini avec chaque message (stockée côté client, jamais persistée côté serveur)
  - Reçoit {type: "init", api_key: "...", participant_id: "..."}: crée StoryState, StudentProfile, AnimationCache. Configure le client Gemini avec la clé API.
  - Reçoit {type: "generate_initial", skill_objectives: [...]}: appelle generate_branches(story_state=None, n=3), renvoie les 3 scenes avec sprite_code et branch_summary
  - Reçoit {type: "generate_one_more"}: appelle generate_one_more(), renvoie 1 scène supplémentaire
  - Reçoit {type: "select_story", index: N}: initialise story_state avec la scène choisie, renvoie la scène complète pour affichage full-size
  - Reçoit {type: "audio", data: base64}: passe à NarrationLoop.on_audio_chunk, renvoie animations
  - Reçoit {type: "branch_choice", index: N}: charge la branche choisie, envoie la scène
  - Sur scene_complete: appelle branch_generator avec student_profile, envoie les 3 options
  - Idle timer côté serveur: détecte le timeout et envoie l'animation d'hésitation

login.html:
- Fond sombre, centré
- Champ "Gemini API Key" (type=password)
- Champ "Participant Number" (type=text)
- Bouton "Ok" qui stocke les valeurs en sessionStorage et redirige vers /selection

selection.html:
- Titre "Let's tell a story together!" en gros, centré, police ronde/enfantine
- Zone de thumbnails: 3 mini-canvas (140x90 chacun, pixel art au scale 1.5x) en ligne
- Sous chaque canvas, le branch_summary (1-2 phrases)
- Bouton "I want to see one more" qui ajoute un thumbnail (appel WebSocket generate_one_more)
- Au clic sur un thumbnail: envoie select_story, redirige vers /story
- Pendant la génération: skeleton loaders animés à la place des thumbnails
- Intègre engine.js pour rendre les previews dans les mini-canvas

story.html:
- Canvas pixelart full-size (280x180, scale 3x = 840x540), centré
- Bouton push-to-talk: grande barre en bas "Hold SPACE to speak" avec feedback visuel (pulsing quand actif, vert quand enregistre)
- Zone de feedback: texte discret montrant la transcription
- Après scene_complete: les 3 thumbnails suivants apparaissent en-dessous (même style que selection.html)
- Intègre engine.js, animations.js, narration.js, scene_picker.js
- Gestion WebSocket complète

narration.js:
- Capture audio via MediaRecorder quand espace est pressé
- Encode en base64 et envoie via WebSocket
- Reçoit les animations et les passe à AnimationRunner

scene_picker.js:
- Composant réutilisable pour afficher N thumbnails avec mini-canvas
- Utilisé sur selection.html (initial) ET story.html (entre les scènes)
- Au clic: envoie le choix via WebSocket

style.css:
- Fond sombre (#0a0a10)
- Police système, titres en police ronde
- Thumbnails avec border-radius, hover effect (léger scale up + glow)
- Canvas avec border-radius
- Bouton push-to-talk large et accessible
- Responsive: thumbnails en colonne sur mobile
- Design enfantin mais pas infantile
```

## Step 12: Session Analytics

```
Lis le CLAUDE.md, section "Post-session Analytics". Implémente src/analytics/session_report.py.

Fonction: generate_report(session_log: dict, student_profile: StudentProfile) -> str

Le session_log contient: toutes les scènes, tous les TranscriptionResult, toutes les animations jouées et leur outcome (enfant a corrigé ou non).

Appelle Gemini 3 Flash (thinking_level: medium) avec un prompt qui demande:
- Résumé des erreurs récurrentes
- Efficacité de chaque type d'animation (taux de correction)
- Progression SKILL scène par scène
- Impact de l'adaptation du student_profile sur les scènes suivantes
- Recommandations pour la prochaine session

Retourne le rapport en Markdown.

Ajoute un endpoint POST /api/report dans app.py qui génère le rapport à la fin de la session.

Écris un test avec un session_log fictif et vérifie que le rapport contient les sections attendues.
```

## Step 13: Integration Test

```
Lis le CLAUDE.md. Écris un test d'intégration end-to-end dans tests/test_integration.py.

Ce test simule une session complète:
1. Choix de l'enfant: brave rabbit, enchanted forest, descriptive_adjectives + spatial_prepositions
2. Génération de Scene 1
3. Rendu de Scene 1 (vérifie que le sprite code s'exécute sans erreur)
4. 3 utterances simulées (mockées):
   - Utterance 1: "there's a rabbit" (manque descripteurs) -> discrepancy property_color -> animation générée
   - Utterance 2: "the brown fluffy rabbit" (correction après animation) -> student_profile mis à jour
   - Utterance 3: narration complète -> scene_progress >= threshold
5. Génération de 3 branches
6. Choix de la branche 1
7. Vérification que Scene 2 reprend les entités de Scene 1
8. Vérification que le student_profile de Scene 2 reflète les erreurs de Scene 1

Ce test peut mocker les appels Gemini avec des réponses JSON pré-construites pour être déterministe, mais doit aussi avoir une version @pytest.mark.integration qui fait les vrais appels API.
```
