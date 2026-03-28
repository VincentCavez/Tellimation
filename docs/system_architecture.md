# System Architecture — Live Interaction Pipeline

## Vue d'ensemble

```
Audio (enfant parle)
       │
   Transcription (Chirp 3 STT)
       │
  ┌────┴────┐
  │         │
Resolution  MISL Detection
  Check     (Gemini lightweight)
  │         │
  └────┬────┘
  Update mention_counts
       │
  ┌────┴──────────────────┐
  │                       │
Path A: Corrections    Path B: Enrichment
(Gemini)               Deterministic selector
                       puis Gemini
  │                       │
  └────┬──────────────────┘
  Animation Handler
  (déterministe)
       │
  WebSocket → Client
  AnimationRunner.playLoop()
```

---

## 1. Capture Audio & Transcription

**Client** : Push-to-talk (touche Espace). `MediaRecorder` capture l'audio, envoie les bytes bruts via WebSocket (binary frame).

**Serveur** : `_handle_audio()` dans `src/ui/app.py`

**Transcription** : `transcribe_audio()` dans `src/narration/transcription.py`
- **Modèle** : Google Cloud Speech-to-Text V2, Chirp 3
- **Input** : `audio_bytes`, `narration_history` (utterances précédentes pour contexte), `narrative_text`
- **Output** : `str` — texte transcrit
- **Latence** : ~1-2s

---

## 2. Resolution Check — `assess_resolution()`

`src/interaction/discrepancy_assessment.py`

```python
async def assess_resolution(
    api_key: str,
    utterance_text: str,
    previous_rationale: Optional[str] = None,  # .description du Discrepancy précédent
    scene_description: str = "",                # champ scene_description du JSON d'histoire
) -> Optional[bool]:
```

- **But** : Vérifier si l'utterance actuelle de l'enfant adresse le feedback précédent (correction ou suggestion)
- **Prompt Gemini** : "Did the child address the feedback?" — generous, si tentative raisonnable → resolved
- **Output** : `True` (resolved), `False` (unresolved), `None` (pas de feedback précédent)
- **Modèle** : `gemini-3-flash-preview`, thinking budget par défaut (512 tokens)

**Dépendances** :
- `previous_rationale` : stocké sur `session._study_previous_discrepancy.description`
- `scene_description` : du JSON d'histoire (`scene["scene_description"]`)

---

## 3. MISL Detection — `detect_misl_elements()`

`src/interaction/discrepancy_assessment.py`

```python
async def detect_misl_elements(
    api_key: str,
    utterance_text: str,
    scene_data: Optional[Dict[str, Any]] = None,
) -> List[str]:
```

- **But** : Identifier quels éléments MISL l'enfant a naturellement produits dans son utterance
- **Output** : Liste de codes MISL, ex: `["CH", "A", "ENP"]`
- **Thinking budget** : 256 tokens (léger)
- **15 codes possibles** : CH, S, IE, A, CO, IR, P (macro) + ENP, SC, CC, M, L, ADV, G, T (micro)

**Tourne en parallèle** avec le Resolution Check (via `asyncio.gather`).

---

## 4. Mise à jour du Mention Counter

Après les deux appels parallèles. Sur `session.current_scene_log.mention_counts` (`Dict[str, int]`, 15 éléments initialisés à 0 par scène).

```python
for code in detected_misl:
    mention_counts[code] += 1
```

Le selector déterministe lit ces compteurs pour décider quel élément MISL proposer.

---

## 5. Path A — Error Correction (Gemini)

`src/interaction/discrepancy_assessment.py`

```python
async def assess_corrections(
    api_key: str,
    utterance_text: str,
    story_so_far: List[str],          # toutes les phrases acceptées dans l'HISTOIRE entière
    scene_description: str,            # description détaillée de la scène (obligatoire)
    character_names: Optional[Dict[str, str]] = None,  # entity_id → prénom donné par l'enfant
    entities_in_scene: Optional[List[str]] = None,      # IDs valides: ["boy", "grandmother", "dog", "box"]
) -> tuple[list[Discrepancy], list[dict[str, str]]]:
```

**Prompt système** (`CORRECTION_SYSTEM_PROMPT`) contient :
- Instructions pour détecter TOUTES les erreurs (grammaticales + narratives)
- **Correction intents** : chargés dynamiquement depuis les 20 grammar JSONs (`animations/grammar/*.json`). Chaque animation avec un `correction_intent` non-null est listée :
  ```
  - I1 (Spotlight) [targets: entity, duo, group, scene]: Child misidentified...
  - D4 (Interjection) [targets: entity, duo, group]: Child made a SEVERE grammatical error...
  - A2 (Flip) [targets: entity, duo, group]: Child described the WRONG ACTION...
  ```
- 7 sections CRITICAL : tolérance actions, grammaire, temps, adjectifs, répétition, hallucinations, duo/group ordering
- Règles de target : entity (1 ID), duo (2 IDs, actor puis receiver), group (3+), scene (["scene"])

**Prompt utilisateur** contient :
- `scene_description` — texte complet de la scène
- `entities_in_scene` — liste exacte des IDs valides (pour éviter les IDs inventés)
- `utterance_text` — phrase de l'enfant
- `story_so_far` — phrases acceptées
- `character_names` — prénoms donnés

**Dérivation de la catégorie** : `_category_from_animation_id()` — cache global qui lit `"category"` de chaque grammar JSON. Ex: `"D1"` → `"Discourse"`, `"P2c"` → `"Property"`. On ne fait PAS confiance au champ `"type"` retourné par Gemini.

**Output** :
```python
Discrepancy(
    pass_type="correction",
    type=category,                    # dérivé de animation_id via grammar JSON
    target_entities=["boy", "box"],   # IDs d'entités
    description="rationale...",       # explication child-friendly
    animation_id="A2",               # ID court de l'animation
    correction_word="is putting",     # uniquement pour D4 (Interjection)
)
```
+ `name_assignments: [{"entity_id": "boy", "name": "Pete"}]` (détection de prénoms)

**Note** : `misl_elements` est toujours vide `[]` pour les corrections — pas pertinent.

---

## 6. Path B — Enrichment (Déterministe + Gemini)

Deux étapes séquentielles.

### 6a. Sélection déterministe — `select_misl_candidates()`

`src/interaction/misl_selector.py`

```python
def select_misl_candidates(
    misl_targets: Dict[str, Any],       # du JSON d'histoire: {"macro": {...}, "micro": {...}}
    mention_counts: Dict[str, int],     # compteur per-scene, 15 éléments
    study_log_entries: List[Dict],       # entrées du study_log pour résolution history
) -> Tuple[Optional[str], Optional[List[str]], Dict[str, Any]]:
```

**Algorithme macro** (ordre fixe : `CH > S > IE > A > CO > IR > P`) :
1. Filtrer les éléments **présents dans la scène** (`misl_targets["macro"][code]` non-null/non-vide)
2. Filtrer < 3 mentions — **HARD GATE** : si aucun ne passe, on tombe aux micros
3. Filtrer unresolved (dernière occurrence non résolue ou jamais vue) — **relaxable** : si aucun ne passe, ignorer ce filtre
4. Prendre le premier dans l'ordre de priorité

**Algorithme micro** (si aucun macro ne survit au filtre < 3) :
- Codes : `ENP, SC, CC, M, L, ADV, G, T`
1. Filtrer présents dans la scène (`misl_targets["micro"][code]`)
2. Filtrer < 3 mentions — **relaxable**
3. Filtrer unresolved — **relaxable**
4. **Shuffle aléatoire** des candidats restants (pour éviter le biais positionnel du LLM)

**Output** : `(macro_selected, micro_candidates_shuffled, trace)`
- Si macro : `("CH", None, trace)`
- Si micro : `(None, ["ENP", "ADV", "SC"], trace)`
- Si rien : `(None, None, trace)` → pas d'enrichment

**Shortcut CH → Nametag** : Si le macro sélectionné est `"CH"` et qu'il existe des entités non nommées (pas dans `session.character_names`), on force `I2_nametag` avec étiquette vide sans appeler Gemini.

**Dépendances** :
- `misl_targets` : du JSON d'histoire. Structure :
  ```json
  {
    "macro": {
      "CH": ["shy boy with round glasses", "tall balloon seller"],
      "S": ["pretty park", "sunny day"],
      "IE": null,
      "A": ["sits on bench", "walks by"],
      ...
    },
    "micro": {
      "ENP": ["round glasses", "colorful balloons"],
      "ADV": ["alone"],
      "SC": [], "CC": [], ...
    }
  }
  ```
- `mention_counts` : du SceneLog
- `study_log_entries` : pour `_last_resolution_status()` qui scanne les events `"resolution"` dans les logs

### 6b. Enrichment Gemini — `assess_enrichment()`

`src/interaction/discrepancy_assessment.py`

```python
async def assess_enrichment(
    api_key: str,
    utterance_text: str,
    story_so_far: List[str],              # histoire entière
    character_names: Optional[Dict[str, str]],
    misl_targets: Dict[str, Any],          # obligatoire
    entities_in_scene: List[str],          # IDs valides
    macro_selected: Optional[str] = None,  # code unique si macro
    micro_candidates: Optional[List[str]] = None,  # liste shufflée si micro
) -> List[Discrepancy]:
```

**Mode macro** : Gemini reçoit :
- L'élément MISL unique à scaffolder (ex: `"S"` = Setting)
- Les exemples pour cet élément depuis `misl_targets` (ex: `"pretty park, sunny day"`)
- Les **suggestion_intents filtrés** : uniquement les animations dont `misl_elements` contient le code sélectionné. Ex pour `"S"` : `S1_reveal`, `S2_stamp`, `T2_timelapse`
- Les entités présentes
- L'utterance + story so far
- Instruction : produire exactement UNE suggestion

**Mode micro** : Gemini reçoit :
- La liste shufflée des candidats avec exemples pour chacun
- Les suggestion_intents filtrés pour l'union de tous les codes candidats
- Instruction : choisir LE plus pertinent et produire UNE suggestion

**Suggestion intents** : chargés depuis les grammar JSONs, même logique que les correction intents mais champ `"suggestion_intent"`. Filtrés par overlap avec les codes MISL d'intérêt :
```python
def _get_filtered_suggestion_intents(misl_codes: List[str]) -> str:
    # Ne garde que les animations dont misl_elements overlap avec misl_codes
```

**Output** (cap à 1) :
```python
Discrepancy(
    pass_type="suggestion",
    type=category,                    # dérivé de animation_id
    target_entities=["boy"],
    misl_elements=["S"],              # code MISL précis
    description="rationale...",
    animation_id="S1",
)
```

---

## 7. Animation Handler (Déterministe)

### Priorité : erreurs > suggestions

`src/interaction/tellimation.py`

```python
def generate_invocation_array(
    discrepancies: List[Discrepancy],
) -> InvocationArray:
```

**Tri** : corrections d'abord, puis suggestions. Au sein de chaque groupe, par priorité de catégorie :
```
Identity (0) > Count (1) > Space (2) > Action (3) > Property (4) > Relation (5) > Time (6) > Discourse (7)
```

**Cap à 1 animation** par cycle.

### Validation des targets

`_select_animation_for_discrepancy()` :
1. Extraire le short ID (`"P2c"` → `"P2C"`)
2. Charger la définition depuis le grammar JSON
3. Valider que le nombre de targets correspond au `target_type` de l'animation
4. Si mismatch : chercher une alternative dans la même catégorie
5. Si aucune alternative : retourner `None` (skip)

### Chargement des paramètres

`load_animation_params(animation_id, study_log_entries)` :
- Charge les `parameters` du grammar JSON (nom, type, range, default)
- Vérifie le dernier status de résolution dans les logs
- **Première fois ou resolved** → defaults du grammar JSON
- **Dernière fois unresolved** → accentuation : push 40% vers le max + variation aléatoire ±10%

### Sanitization des targets

Après le gather des deux paths, les `target_entities` sont sanitizés via `sanitize_target_entities()` :
- Map les IDs inventés par Gemini vers les vrais IDs (`"red_box"` → `"box"`, par substring match)
- Drop les IDs impossibles à mapper

---

## 8. Dépendances : Grammar JSONs

**Localisation** : `animations/grammar/*.json` (20 fichiers)

**Champs utilisés par le pipeline** :

| Champ | Utilisé par |
|-------|------------|
| `id` | Identification de l'animation (ex: `"I1"`, `"P2c"`) |
| `category` | Dérivation de la catégorie du Discrepancy (ex: `"Identity"`, `"Property"`) |
| `correction_intent` | Injecté dans le prompt correction — décrit quel type d'erreur l'animation corrige |
| `suggestion_intent` | Injecté dans le prompt enrichment — décrit quel enrichissement elle scaffold |
| `misl_elements` | Filtrage des intents pertinents pour le code MISL sélectionné |
| `target_type` | Validation du nombre de targets (`["entity"]`, `["duo"]`, `["group"]`, `["scene"]`) |
| `parameters` | Chargement des defaults et accentuation sur repeat unresolved |

---

## 9. Dépendances : Story JSONs

**Localisation** : `data/study_scenes/*.json`

**Champs utilisés par le pipeline** :

| Champ | Utilisé par |
|-------|------------|
| `scene_description` | Passé à correction + resolution check — texte décrivant tout ce qui est visible |
| `misl_targets.macro` | Selector déterministe — éléments macro dispo dans la scène avec exemples |
| `misl_targets.micro` | Selector déterministe — éléments micro dispo avec exemples |
| `entities_in_scene` | Liste des IDs valides, passée aux prompts + sanitization |
| `entity_urls` | Client-side : chargement des images d'entités pour le rendu HD |

---

## 10. Student Profile

`src/models/student_profile.py`

| Champ | Rôle |
|-------|------|
| `age` | Pas directement utilisé dans le pipeline live actuel |
| `misl_difficulty_profile` | Historique persistant suggestion/résolution par dimension |
| `total_utterances` | Incrémenté à chaque utterance |
| `character_names` (sur session) | Prénoms donnés par l'enfant, persistant entre scènes |

---

## 11. Logging — Structure du `pipeline_cycle`

Chaque cycle produit un entry `"event": "pipeline_cycle"` dans le study_log :

```json
{
  "event": "pipeline_cycle",
  "mention_counts": {"CH": 2, "S": 1, "IE": 0, "A": 1, ...},
  "deterministic_selection": {
    "macro_in_scene": ["CH", "S", "A", "IR"],
    "macro_under_3": ["S", "A", "IR"],
    "macro_unresolved": ["S", "IR"],
    "macro_selected": "S",
    "micro_in_scene": null,
    "micro_under_3": null,
    "micro_unresolved": null,
    "micro_candidates_shuffled": null,
    "micro_gemini_selected": null
  },
  "errors_found": [{"animation_id": "A2", "targets": ["boy"], "category": "Action", "desc": "..."}],
  "suggestion": {"misl_element": "S", "rationale": "...", "targets": ["scene"], "animation_id": "S1"},
  "selected": {"source": "error"|"suggestion"|"no_action", "animation_id": "A2", "targets": ["boy"]},
  "action": "triggered"|"control_suppressed"|"no_action",
  "condition": "animation"|"control"
}
```

---

## 12. Client-Side : Animation Execution

**Message WebSocket** (serveur → client) :
```json
{
  "type": "animation",
  "template": "spotlight",
  "params": {"dimStrength": 0.85, "entityPrefix": "boy"},
  "duration_ms": 3000
}
```

**Client** (`study_story.js` → `handleAnimation()`) :
- `AnimationRunner.playLoop(spec)` — joue l'animation en boucle
- Le runner opère sur un `PixelBuffer` HD (résolution complète, `HD_SCALE = 1`)
- `_computeEntityBounds()` utilise les bounds pré-calculés depuis `hdEntityData` (masques d'entités originaux, avant chevauchement)
- `_perTargetWrapper()` : les animations duo/group sont exécutées séparément par entité
- Le runner snapshot/restore le buffer à chaque frame pour ne pas corrompre les pixels
