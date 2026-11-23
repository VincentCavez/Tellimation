"""Simulated child narrator, used to exercise the study pipeline without a participant.

A recording is produced in three steps:
  1. `plan()` draws, from the profile's traits and a seeded RNG, what kind of
     recording this is (new narration, reaction to an animation, repetition,
     missed recording...), which mistakes it carries and what the speech
     recogniser does to it. The plan is the ground truth the pipeline's
     verdict is later compared with.
  2. `speak()` asks a Gemini model to write what the child says, in the
     register of real session transcripts with a similar profile (taken from
     OTHER stories, so nothing real is copied into the simulated story).
     Recordings that need no wording (a stray token, the same recording sent
     again, "I don't know") are produced without the model.
  3. `recognise()` clips the recording the way push-to-talk and the recogniser
     do in the real sessions (missed start, cut end, no final full stop).

The child model never sees what the pipeline wrote about a recording: an
animation reaches it as what it looks like and where it played, nothing more.
Nothing here decides what the pipeline does: the text is sent to the real
study server (see simulate_sessions.py).
"""

from __future__ import annotations

import csv
import json
import math
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

REPO = Path(__file__).resolve().parents[2]
REAL_SESSIONS_CSV = REPO / "session transcripts" / "tellimations_sessions.csv"

csv.field_size_limit(sys.maxsize)

# Content mistakes: definition given to the child model, and the animation
# categories (first letter of the animation id) that count as "caught".
SLIPS: Dict[str, Dict[str, Any]] = {
    "identity": {"weight": 0.35, "expect": "I",
                 "text": "uses a neighbouring word for something the drawing does not settle (which bird or animal exactly, "
                         "how two people are related, what kind of place or building this is), "
                         "never for a thing that is plainly recognisable, and never for a character or thing "
                         "this child has already named in this story"},
    "action": {"weight": 0.25, "expect": "AD",
               "text": "misreads what a character is doing: names a neighbouring action that this very pose could be "
                       "taken for at a glance, never an action the drawing rules out"},
    "property": {"weight": 0.15, "expect": "PD",
                 "text": "misreads a feeling from the face or the pose drawn (a neighbouring feeling, "
                         "the way shy can be taken for sad)"},
    # weight 0: every wording tried gave far-fetched sentences ("the bird opens his mouth to eat the train")
    "cause": {"weight": 0.0, "expect": "RAD",
              "text": "gets wrong who is behind what happens: takes an accident for something done on purpose, "
                      "or thinks the character standing closest did it; nothing odder than that"},
    "place": {"weight": 0.08, "expect": "SI",
              "text": "gets a position slightly wrong (next to instead of on, in front of instead of behind), "
                      "where the drawing can be read both ways"},
    "absent": {"weight": 0.07, "expect": "CI",
               "text": "talks as if a character from an earlier picture were here, although it is not in this picture"},
}

# Grammar patterns a profile can own. A child keeps the same one or two for the
# whole session; examples are deliberately not about the study pictures.
GRAMMAR: Dict[str, str] = {
    "bare_verb": "leaves a verb bare where an ending is needed ('the cat want milk', 'yesterday she walk home')",
    "no_aux": "leaves out 'is' or 'are' before an -ing verb ('the cat sleeping on the sofa')",
    "tense_shift": "slips between present and past ('she opens the door and went out', 'the cat was sleeping' "
                   "about a picture in front of them)",
    "overregular": "puts a regular ending on an irregular word ('goed', 'mouses')",
    "plural_is": "uses 'is' or 'was' after two people or a plural ('the cat and the dog is friends')",
    "no_article": "drops 'the' or 'a' before a noun ('cat is on sofa')",
    "preposition": "picks the wrong small word ('she is looking to the cat', 'on the morning')",
    "he_she": "mixes up he and she, or his and her, for one character ('the lady lost his keys')",
}

# A tense slip takes a different shape each time (one fixed shape, "is V-ing and V-ed", gave the simulation away)
TENSE_FORMS = [
    "says this whole recording in the past, although the child has been telling the story in the present",
    "puts the first verb in the past and comes back to the present for the rest ('she opened the door and she is going out')",
    "says 'was' or 'were' where 'is' or 'are' is meant ('the cat was sleeping on the sofa', about the picture in front of them)",
    "puts the last verb of the sentence in the past ('she opens the door and went out')",
]

# {restart} and {filler} are drawn for each recording, so that a child's hesitations do not all look alike
DISFLUENCY: Dict[str, str] = {
    "restart": "{restart}, with no pause mark",
    "abandon": "does not finish: the recording stops on an unfinished phrase",
    "repeat_phrase": "says twice in a row the two linking words this sentence happens to use",
    "repeat_word": "says one word twice in a row",
    "filler": "begins with '{filler}', which the recogniser kept",
}

RESTARTS = ["breaks off after the verb and says the phrase again from its subject, with the verb in another form",
            "breaks off after the first two or three words and starts again with a different subject",
            "breaks off in the middle and repeats the last three or four words before going on",
            "says the start of a clause, drops it, and says the whole clause in other words"]
FILLERS = ["Um", "Uh", "Mmm", "So", "Okay", "Well", "Like", "Oh"]

# What a child who takes an animation up says next (never the tutorial's wording)
UPTAKE = {
    "I1": "says something about that character or thing; about the place, if it played on the whole picture",
    "I2": "says who that character is",
    "C": "says who is, or is not, in this picture",
    "A": "says what that character is doing",
    "P1": "says what it looks like",
    "P2": "says how that character feels",
    "S": "says where it is",
    "T": "says what happened before, or what happens next",
    "R": "says what one of them does to the other",
    "D1": "says what that character says",
    "D2": "says what that character wants or thinks",
    "D3": "says what has just happened to it",
    "D4": "says the previous sentence again with one word in another form",
}

VOCABULARY = {
    7: "everyday words only; plain nouns; almost no describing words",
    8: "everyday words; basic feelings (happy, sad, scared)",
    9: "some more exact nouns and verbs; basic feelings plus one or two finer ones",
    10: "fairly exact words; verbs about what people think or want",
    11: "exact and varied words; now and then a guess about what someone thinks or will do",
}

# Habits a child keeps for the whole session. Each simulated child draws its own,
# and a choice already taken by another child is made less likely.
HABITS: Dict[str, List[str]] = {
    "meta": ["just names what the animation played on, with no verb",
             "says that the character or thing is gone or not there any more",
             "says that something is wrong or funny with the picture",
             "asks the app to stop or to wait, in two or three words",
             "says that the character or thing looks different now"],
    "focus": ["what the characters are doing", "how the characters feel", "where things and people are",
              "the things people hold, drop or carry", "what is going to happen next", "why things happen",
              "small things in the background", "what the animals are doing"],
    "pronouns": ["names the character again in almost every sentence, rarely 'he' or 'she'",
                 "names a character once per picture, then mixes the name and 'he', 'she' or 'they'",
                 "names a character once per picture, then says 'he', 'she', 'it' or 'they'"],
    "contractions": ["uses a short form ('he's', 'there's', 'it's', 'doesn't') in about one recording out of six",
                     "uses short forms ('he's', 'they're', 'it's', 'doesn't', 'gonna') in about one recording out of three"],
    "adult_confirm": ["Yeah", "Mhm", "Right", "Okay", "Uh-huh", "Yes", "Alright", "No", "Hmm", "Oh"],
    "adult_prompt": ["What else?", "And then?", "What's happening?", "Tell me more.", "What do you see?", "Keep going.",
                     "Who's that?", "What's he doing?", "Anything else?", "Go on.", "What about this one?", "Look here."],
}

# The words a child may use for each character, plainest first. Each simulated
# child draws its own, so that eight narrators do not share one way of naming.
LABELS: Dict[str, Dict[str, List[str]]] = {
    "training": {"girl": ["the girl"], "boy": ["the boy", "the kid"], "crab": ["the crab"],
                 "grandmother": ["the grandma", "the lady", "his grandma", "the grandmother"],
                 "dog": ["the dog", "the puppy"], "box": ["the box", "the tin"]},
    "A": {"boy": ["the boy", "the kid", "the little boy"],
          "seller": ["the man", "the balloon man", "the man with the balloons", "the balloon seller"],
          "bird": ["the bird", "the little bird"], "balloon": ["the balloon", "his balloon"]},
    "B": {"boy": ["the boy", "the kid"], "mother": ["the mom", "his mom", "the lady", "his mother"],
          "monkey": ["the monkey"], "bag": ["the bag", "the purse", "the handbag"]},
    "C": {"girl": ["the girl", "the little girl", "the younger girl"],
          "sister": ["the big girl", "her sister", "the big sister", "the older girl", "her older sister"],
          "firefly": ["the bug", "the fly", "the firefly", "the lightning bug"], "house": ["the house", "home"]},
    "D": {"conductor": ["the man", "the train man", "the old man", "the driver", "the conductor"],
          "train": ["the train"], "goose": ["the bird", "the big bird", "the goose"], "tractor": ["the tractor"]},
}

NAMES = ["Max", "Ben", "Sam", "Jack", "Tom", "Noah", "Luke", "Eli", "Finn", "Owen", "Theo", "Adam", "Ryan", "Josh",
         "Emma", "Lily", "Mia", "Zoe", "Anna", "Ruby", "Ella", "Lucy", "Grace", "Ivy", "Sara", "Nora", "Kate", "Amy"]

STUCK = ["I don't know.", "I don't know what else.", "Um.", "That's it.", "Nothing else.", "I'm done.", "Okay.",
         "Um, I don't know.", "I don't know. I don't know.", "Nothing.", "I can't see.", "That's all.", "Done.", "Dunno."]

# What the recogniser prints when a recording holds next to nothing.
# (none of these strings is taken from a real session)
STRAY = ["the", "and", "Okay", "so", "it", "Yeah.", "oh", "is", "then", "Hm.", "I", "to", "um", "two", "no", "in", "he", "Right."]

SYSTEM_PROMPT = """You help test a storytelling app for children. The app shows a picture; \
the child holds a key and tells the story aloud; a speech recogniser turns each recording into text; \
the app may then play a wordless animation on the picture.

You write ONE recording of a SIMULATED child, whose profile and turn plan are given: what the child really said, \
and what the speech recogniser printed. The printed text is fed to the app's assessment pipeline.

Rules
- Recogniser output only: spoken words with ordinary capitals and punctuation. No speaker labels, \
no stage directions, no quotation marks. A recogniser never prints "..." or dashes.
- The PICTURE the child is looking at is attached. Everything the child says is about what is drawn in it: \
a character, object, place or event that cannot be seen there is never mentioned. The written description is only \
a check on what the drawing shows (the app assesses the child against it): never reuse its wording. \
Talk about one or two things per recording, as children do.
- Follow the TURN PLAN exactly: kind of recording, approximate length, mistakes to include and nothing else. \
Apart from the planned mistakes and the child's established wrong words, what the child says agrees with the picture. \
A planned content mistake is a misreading of something that IS drawn: look at the picture and pick a detail a child \
could really take for something else. It never contradicts what anyone can plainly see, and it is a sensible one for a child of this age: a listener \
looking at the picture sees at once why the child thought so, and would not smile at it. If nothing in this picture \
can be misread in the planned way, make no content mistake and leave "mistakes" empty.
- Use this child's own words for the characters (given with the picture) and keep them through the story.
- These children tell what happens and how people feel. Describing words for colour or size are rare in their \
stories: use none, unless two things could not be told apart otherwise. Words like 'very' and 'really' are rare too.
- A child mentions one thing at a time: never a list of objects, never an inventory of the background.
- This is speech, not writing: one plain sentence per recording unless the plan says two. No adverbs of manner \
('carefully', 'warmly'), no storybook phrases, no closing remarks about the story.
- The plan says whether this recording gives a reason. When it does not, there is no 'because', 'so', 'since' or 'while'.
- A child who changes their mind just says the new version. They do not announce it: no 'wait', 'actually', \
'no, it is not X, it is just Y'.
- Hesitations appear only when the plan asks for one, and only in the form the plan gives. \
Never double a small word ('the the', 'is is') on your own.
- A grammar mistake appears only when the plan asks for one, in the form the plan gives, inside a sentence as simple \
as the child's other sentences. All other grammar is correct, informal speech.
- Imitate the register of the reference transcripts (sentence shape, level), never their content, \
and never an odd turn of phrase found in them.
- Do not tack 'now' onto the end of a sentence.
- Do not write baby talk, and do not make every recording messy.

Answer with JSON only:
{"child_said": "<only the child's own words, as really spoken>",
 "transcript": "<what the recogniser printed for the whole recording>",
 "misheard": [{"said": "<word or words really spoken>", "heard": "<what the recogniser printed instead>"}],
 "mistakes": [{"type": "identity|action|property|cause|place|absent|grammar", "detail": "<the wrong word or form, and what is right>"}]}
"transcript" equals "child_said" unless the plan says the recogniser mis-heard something or an adult was recorded. \
List in "mistakes" every mistake actually present in the child's own words, including carried-over wrong words \
(never a recogniser mis-hearing); use empty lists when there is nothing to list."""

ADULT = {
    "dialogue": "The adult sitting next to the child talks through the recording, more than the child does: asks the same "
                "thing two or three times in slightly different words, repeats what the child just said, "
                "or tells the child how to use the app. The child answers in one to three words each time. "
                "The adult is a parent, not a teacher: talks fast and casually, in short forms ('what's', 'he's', 'don't'), "
                "in everyday words (the boy, the man, the train), with half-finished questions; can be impatient or "
                "doubtful, disagree with the child, answer in the child's place, or talk about the keys and the screen; "
                "hardly ever praises, and does not tidy up the child's grammar. "
                "The recogniser prints it all in one run with no speaker labels, and not tidily: an answer can come before "
                "the question is over, a sentence can be left hanging, a remark that is not about the story can slip in. "
                "The adult's words do not count in the length.",
    "before": "The recording also caught one short prompt from the adult next to the child, before the child's words, "
              "with no speaker label. The child answers that prompt in a few words, the way one answers a question, "
              "not with a full story sentence.",
    "after": "The recording also caught the adult reacting after the child's words (a word of approval, or a short prompt "
             "to say more), with no speaker label.",
    "echo": "The recording also caught the adult repeating part of what the child said, as a question or to confirm it; "
            "the child may answer with one word. No speaker labels.",
    "only": "Only the adult is heard in this recording, prompting the child, who stays silent or says one word. "
            "\"child_said\" holds only that one word, or is empty.",
}


def load_reference_utterances(participants: List[str]) -> Dict[str, List[Dict[str, str]]]:
    """Real transcriptions of the reference sessions: {participant: [{story, text}]}."""
    out: Dict[str, List[Dict[str, str]]] = {p: [] for p in participants}
    if not REAL_SESSIONS_CSV.exists():
        return out
    with open(REAL_SESSIONS_CSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["participant_id"] in out and row["event"] == "transcription" and row["text"].strip():
                out[row["participant_id"]].append({"story": row["story"], "text": row["text"].strip()})
    return out


# A recogniser that misses an ending still prints a real word: 'walk' for 'walked', never 'scar' for 'scared'
_PAST = {"walked": "walk", "looked": "look", "jumped": "jump", "picked": "pick", "pushed": "push", "pulled": "pull",
         "climbed": "climb", "dropped": "drop", "popped": "pop", "grabbed": "grab", "stopped": "stop", "opened": "open",
         "wanted": "want", "helped": "help", "turned": "turn", "started": "start", "tripped": "trip", "pecked": "peck",
         "handed": "hand", "asked": "ask", "watched": "watch", "happened": "happen", "called": "call", "played": "play",
         "scared": "scare", "chased": "chase", "smiled": "smile", "waved": "wave", "liked": "like", "moved": "move",
         "closed": "close", "landed": "land", "followed": "follow", "kicked": "kick", "yelled": "yell",
         "shouted": "shout", "pointed": "point", "reached": "reach", "stayed": "stay", "tried": "try", "cried": "cry"}
_SHORT_FORMS = {"he's", "she's", "it's", "that's", "there's", "here's", "what's", "who's", "where's", "how's", "let's"}


def _unending(word: str) -> str:
    """Undo the recogniser-style ending of a word (e.g. past tense or plural) to the form a young child would say."""
    core = word.rstrip(".,?!")
    tail = word[len(core):]
    low = core.lower()
    if low in _SHORT_FORMS:
        return word  # 'hes' is not something a recogniser prints
    if low.endswith("'s") and len(core) > 3:
        return core[:-2] + "s" + tail
    if low.endswith("ed"):
        base = _PAST.get(low)
        return (base.capitalize() if core[0].isupper() else base) + tail if base else word
    if (low.endswith("s") and len(core) > 4 and not low.endswith(("ss", "us", "is", "as", "'s", "ys", "ies", "oes", "ches", "shes", "xes", "zes", "sses"))
            and low not in ("always", "perhaps", "towards", "glasses", "pajamas")):
        return core[:-1] + tail
    return word


INSERTS = ["Okay.", "Yeah.", "Hm.", "so", "and", "Oh.", "Mhm.", "okay"]


# Short common words a cut-off word can be printed as ('sand' for a cut 'sandwich', 'in' for 'into')
_WHOLE = set("""a an and are as at ball be bed big bird boy box but can car cat day do dog down for get girl go had has he
her him his in is it man me my no not of on one or out pop put ran run sand see she so the to top train tree up was we
back bag ban bar bat bell bit book bus cap cart cold corn door ear eat end eye far fire fly for fun hand hat head hill
ink kid lay leg let light lit low mad map mark men moon mud net new old pan pen pet pig play rain red rest road rock sat
sea set sis sit star step sun tea ten tin toy try two wall war wet win wind wood""".split())
_SMALL = set("the a an his her their its to of in on at and with by for".split())


def _cut_word(word: str) -> Optional[str]:
    """The start of a word cut by the key, when that start is itself a common word; else None."""
    core = "".join(c for c in word.lower() if c.isalpha())
    for n in range(len(core) - 1, 1, -1):
        # 'fell do' for 'fell down' reads as cut letters, not as a word the recogniser heard
        if core[:n] in _WHOLE and (n >= 3 or core[:n] in ("in", "on")):
            return core[:n]
    return None


def recognise(text: str, asr: Dict[str, Any], rng: random.Random, keep: Optional[int] = None,
              inserts: Optional[List[str]] = None) -> Tuple[str, List[str]]:
    """Clip a recording as push-to-talk and the recogniser do. `keep`: words left of a missed recording."""
    words, ops = text.split(), []
    clip = asr.get("clip")
    if clip and len(words) >= 3:
        drop = len(words) - keep if keep else rng.randint(1, min(4, len(words) - 2))
        drop = max(1, min(drop, len(words) - 1))
        if clip == "tail":
            lost, words = words[-drop], words[:-drop]
            part = _cut_word(lost)
            if part and rng.random() < 0.4:
                words.append(part)  # the key was released in the middle of a word
            elif len(words) > 2 and words[-1].lower() in _SMALL and rng.random() < 0.5:
                words = words[:-1]  # a cut does not always fall just before the noun
        else:
            words = words[drop:]
        ops.append(f"{clip}-{drop}")
    if asr.get("ending"):
        # An ending the recogniser did not catch: a possessive 's, a plural or verb -s, a past -ed
        spots = [i for i, w in enumerate(words) if i and _unending(w) != w]
        if spots:
            i = rng.choice(spots)
            words[i] = _unending(words[i])
            ops.append("ending")
    if asr.get("insert"):
        token = rng.choice(inserts or INSERTS)
        words = [token] + words if rng.random() < 0.5 else words + [token]
        ops.append("insert")
    out = " ".join(words)
    if clip == "tail" or asr.get("no_punct"):
        stripped = out.rstrip(".?!,;")
        if stripped != out:
            out = stripped
            ops.append("no_punct") if clip != "tail" else None
    if (clip == "head" or asr.get("lower")) and out[:2] not in ("I ", "I'") and out[:1].isupper():
        out = out[:1].lower() + out[1:]
        ops.append("lower") if clip != "head" else None
    return out, ops


def draw_habits() -> Dict[str, Dict[str, Any]]:
    """Habits of every profile, drawn together so that two children seldom share one.

    Deterministic: depends only on profiles.json, whichever profiles are run.
    """
    profiles = json.loads(Path(__file__).with_name("profiles.json").read_text())["profiles"]
    rng = random.Random("habits")
    taken: Dict[Any, int] = {}

    def pick(key: Any, options: List[str], weights: Optional[List[float]] = None, k: int = 1,
             penalty: float = 0.3) -> List[str]:
        """Draw `k` distinct options, optionally weighted, with weights reduced for options already used for this key."""
        chosen: List[str] = []
        for _ in range(k):
            pool = [o for o in options if o not in chosen]
            w = [(weights[options.index(o)] if weights else 1.0) * penalty ** taken.get((key, o), 0) for o in pool]
            choice = rng.choices(pool, weights=w)[0]
            taken[(key, choice)] = taken.get((key, choice), 0) + 1
            chosen.append(choice)
        return chosen

    def ranked(options: List[str], level: float) -> List[float]:
        """Weights leaning toward the option whose rank matches `level` (0 = first, 1 = last)."""
        return [math.exp(-abs(i / max(len(options) - 1, 1) - min(max(level, 0.0), 1.0)) * 2.5) for i in range(len(options))]

    out: Dict[str, Dict[str, Any]] = {}
    for prof in profiles:
        skill = (prof["age"] - 7) / 4 + (0.15 if prof["diagnosis"] == "self" else 0.0)
        habits: Dict[str, Any] = {"labels": {}}
        for story, entities in LABELS.items():
            # Older and stronger narrators lean toward the more exact word (later in the list)
            habits["labels"][story] = {e: pick((story, e), opts, ranked(opts, skill))[0] for e, opts in entities.items()}
        habits["meta"] = pick("meta", HABITS["meta"], k=2)
        habits["focus"] = pick("focus", HABITS["focus"], k=2)
        habits["pronouns"] = pick("pronouns", HABITS["pronouns"], ranked(HABITS["pronouns"], skill))[0]
        habits["contractions"] = pick("contractions", HABITS["contractions"], [0.6, 0.4])[0]
        habits["short_form_rate"] = 1 / 6 if habits["contractions"] == HABITS["contractions"][0] else 1 / 3
        if prof["traits"]["p_adult"] > 0:  # each adult gets words no other adult has
            habits["adult_confirm"] = pick("adult_confirm", HABITS["adult_confirm"], k=2, penalty=0.01)
            habits["adult_prompt"] = pick("adult_prompt", HABITS["adult_prompt"], k=3, penalty=0.01)
        habits["names"] = rng.sample(NAMES, len(NAMES))
        habits["stuck"] = pick("stuck", STUCK, k=3)
        out[prof["id"]] = habits
    return out


# Describing words a child rarely volunteers; next to the same noun as in the written description, they give it away
DESCRIBING = set("tall small large big little tiny huge long short wide thin thick round wooden woven stone brick metal "
                 "brown green blue yellow red white black purple pink orange grey gray golden dark bright striped "
                 "fluffy curly leafy winding cobblestone colorful colourful young old sturdy calm gentle lush".split())
_FUNCTION = set("the a an is are was were and or but he she it they him her his its them their to of in on at by up out "
                "with for from into onto over under near this that there here".split())


def echoes(said: str, description: str) -> List[str]:
    """Phrases of the child's that come straight from the written description of the picture."""
    words = [w.strip(".,!?;:'\"").lower() for w in said.split()]
    text = " " + " ".join(w.strip(".,!?;:'\"").lower() for w in description.split()) + " "
    found = [f"{a} {b}" for a, b in zip(words, words[1:]) if a in DESCRIBING and b not in _FUNCTION and f" {a} {b} " in text]
    for i in range(len(words) - 3):
        gram = words[i:i + 4]
        if sum(w not in _FUNCTION for w in gram) >= 3 and " " + " ".join(gram) + " " in text:
            found.append(" ".join(gram))
    return sorted(set(found))


class ChildSimulator:
    def __init__(self, profile: Dict[str, Any], api_key: str, model: str, rng: random.Random) -> None:
        """Set up a simulated child from its profile: Gemini client, reference utterances, usage counters and personal habits."""
        self.profile = profile
        self.traits = profile["traits"]
        self.model = model
        self.rng = rng
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=90_000))
        self.references = load_reference_utterances(profile.get("reference", []))
        self.usage = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        # The child's own habits, drawn once and kept for the session
        mine = draw_habits()[profile["id"]]
        self.labels: Dict[str, Dict[str, str]] = mine["labels"]
        for story, words in profile.get("labels", {}).items():  # a profile can fix its own word for a character
            self.labels.setdefault(story, {}).update(words)
        self.habits: Dict[str, Any] = {**mine, **profile.get("habits", {})}
        self.names: List[str] = mine["names"]
        self.stuck: List[str] = mine["stuck"]
        self.rare_used: set = set()
        self.pictures: Dict[str, bytes] = {}

    # ------------------------------------------------------------------ plan

    def turns_in_scene(self, animated: bool) -> int:
        """Recordings in a scene: 1 + Poisson around the profile's mean, as in the real sessions."""
        mean = self.traits["turns_animated" if animated else "turns_baseline"]
        limit, n, p = math.exp(-max(mean - 1.0, 0.05)), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= limit:
                break
            n += 1
        return min(1 + n, 7)

    def scene_noise(self) -> float:
        """How rough the recogniser is on one picture: clean most of the time, bad in patches."""
        return self.rng.choices([0.0, 0.5, 1.0, 3.0], weights=[0.35, 0.25, 0.25, 0.15])[0]

    def view(self, description: str) -> str:
        """The written description of a picture, given whole next to the picture itself."""
        return " ".join(description.split())

    def _adult(self) -> Optional[str]:
        """Decide whether an adult voice is heard in this recording and in which way (None for no adult)."""
        rate = self.traits["p_adult"]
        if self.rng.random() >= rate:
            return None
        if rate >= 0.4:
            return self.rng.choices(["dialogue", "echo", "only"], weights=[0.6, 0.25, 0.15])[0]
        return self.rng.choices(["before", "after", "echo", "only"], weights=[0.35, 0.2, 0.25, 0.2])[0]

    def plan(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Draw the plan of the next recording from the child's traits and the context (narrate or react to an animation, slips, grammar, disfluency, recognition errors)."""
        t, rng, asr = self.traits, self.rng, self.traits["asr"]
        anim = ctx.get("animation")
        plan: Dict[str, Any] = {"kind": "narrate", "mode": None, "slips": [], "grammar": None, "disfluency": None,
                                "adult": None, "name": None, "length": 0, "reason": False, "fix": False,
                                "asr": {"mishear": None, "ending": False, "insert": False,
                                        "clip": None, "no_punct": False, "lower": False}}
        r = rng.random()
        if anim:
            uptake = t["uptake_correction"] if anim["pass_type"] == "correction" else t["uptake_suggestion"]
            if r < uptake:
                plan["kind"] = "uptake"
            elif r < uptake + t["p_meta"]:
                plan["kind"] = "meta"
            elif r < uptake + t["p_meta"] + 0.25:
                plan["kind"] = "partial"
            elif r < uptake + t["p_meta"] + 0.25 + t.get("p_persist", 0.0):
                # The child holds on to what they said (P1 re-says it after 28% of the animations seen)
                plan["kind"], plan["persist"] = "repeat", True
        else:
            if r < t["p_fragment"]:
                plan["kind"] = "fragment"
            elif r < t["p_fragment"] + t["p_stuck"]:
                plan["kind"] = "stuck"
            elif not ctx["first_in_scene"] and r < t["p_fragment"] + t["p_stuck"] + t["p_repeat"]:
                plan["kind"] = "repeat"

        length = rng.lognormvariate(math.log(t["len_med"]), 0.55)
        plan["length"] = int(max(3, min(t.get("len_max", 30), round(length))))

        if plan["kind"] == "fragment":
            # Nothing usable reached the recogniser: a stray token, or the head or tail of a sentence
            plan["mode"] = rng.choices(["stray", "head", "tail"], weights=[0.35, 0.3, 0.35])[0]
            if plan["mode"] != "stray":
                plan["asr"]["clip"] = "tail" if plan["mode"] == "head" else "head"
                plan["keep"] = rng.choice([1, 1, 2, 2, 3, 4])
            return plan
        if plan["kind"] == "stuck":
            plan["adult"] = self._adult()
            return plan
        if plan.get("persist"):
            plan["mode"] = rng.choices(["same", "shorter", "expand"], weights=[0.4, 0.3, 0.3])[0]
        elif plan["kind"] == "repeat":
            plan["mode"] = rng.choices(["verbatim", "expand", "same"], weights=[0.4, 0.4, 0.2])[0]
            if plan["mode"] == "verbatim":
                return plan

        if plan["kind"] in ("narrate", "partial") and rng.random() < t["p_slip"]:
            kinds = [k for k in SLIPS if k != "absent" or ctx.get("absent")]
            plan["slips"] = rng.choices(kinds, weights=[SLIPS[k]["weight"] for k in kinds])
        grammar_rate = t["p_grammar"] * (0.5 if plan["kind"] == "uptake" else 1.0)
        if self.profile.get("grammar") and plan["kind"] != "repeat" and rng.random() < grammar_rate:
            # A rare pattern shows up once in a session at most
            rare = [g for g in self.profile.get("grammar_rare", []) if g not in self.rare_used]
            if rare and rng.random() < 0.15:
                plan["grammar"] = rare[0]
                self.rare_used.add(rare[0])
            else:
                plan["grammar"] = rng.choice(self.profile["grammar"])
            plan["length"] = min(plan["length"], int(t["len_med"]) + 3)  # mistakes sit in simple sentences
        # Anchors: a reason in 2% (P1, 7 years) and 14% (P16, 11 years) of the real recordings
        reason_rate = 0.02 + 0.03 * (self.profile["age"] - 7) + (0.01 if self.profile["diagnosis"] == "self" else 0.0)
        plan["reason"] = plan["length"] >= 7 and rng.random() < reason_rate
        plan["fix"] = plan["kind"] == "uptake" and rng.random() < 0.55
        plan["short_form"] = rng.random() < self.habits["short_form_rate"]
        plan["two"] = plan["length"] >= 16 and rng.random() < 0.3
        if t.get("p_inner") and plan["kind"] in ("narrate", "partial") and rng.random() < t["p_inner"]:
            plan["inner"] = True  # P16: two thirds of the content words are not in the scene description
        if plan["kind"] == "uptake":
            plan["length"] = min(plan["length"], int(t["len_med"]))
        if self.profile.get("disfluency") and rng.random() < t["p_disfluency"]:
            plan["disfluency"] = rng.choice(self.profile["disfluency"])
        plan["adult"] = self._adult()
        if plan["adult"] == "before":
            plan["length"], plan["two"] = min(plan["length"], 5), False  # an answer to the prompt, not a sentence
        if plan["kind"] == "uptake" and anim and anim["short"] == "I2" and rng.random() < t.get("p_name", 0.0):
            plan["name"] = self.names[len(ctx.get("named", []))]

        # Recogniser trouble comes in bursts: `noise` is drawn per picture (0 = a clean stretch)
        noise = ctx.get("noise", 1.0) * (1.6 if plan["adult"] else 1.0)
        if rng.random() < asr["mishear"] * noise:
            plan["asr"]["mishear"] = rng.choices(["neighbour", "garble"], weights=[0.7, 0.3])[0]
        plan["asr"]["ending"] = rng.random() < asr["mishear"] * 0.6 * noise
        plan["asr"]["insert"] = rng.random() < asr["mishear"] * 0.3 * noise
        c = rng.random()
        if plan["disfluency"] != "abandon":
            plan["asr"]["clip"] = "tail" if c < asr["clip_tail"] else "head" if c < asr["clip_tail"] + asr["clip_head"] else None
        plan["asr"]["no_punct"] = plan["disfluency"] == "abandon" or rng.random() < asr["no_punct"]
        plan["asr"]["lower"] = rng.random() < asr["lower"]
        return plan

    # ---------------------------------------------------------------- prompt

    def _examples(self, story: str, n: int = 10) -> List[str]:
        """Reference recordings from other stories, a seeded sample across the reference sessions."""
        pool: List[str] = []
        refs = [p for p in self.profile.get("reference", []) if self.references.get(p)]
        for rank, pid in enumerate(refs):
            texts = [u["text"] for u in self.references[pid] if u["story"] != story]
            share = n if len(refs) == 1 else (round(n * 0.65) if rank == 0 else n - round(n * 0.65))
            pool += self.rng.sample(texts, min(share, len(texts)))
        return pool

    def _label(self, ctx: Dict[str, Any], entity: str) -> str:
        """Word this child uses for an entity in the current story."""
        return self.labels.get(ctx["story"], {}).get(entity, entity)

    def _animation_text(self, anim: Dict[str, Any], ctx: Dict[str, Any], kind: str) -> str:
        """What the child saw. Never what the pipeline wrote about the recording."""
        short, meaning = anim["short"], kind == "uptake"
        targets = ["the whole picture" if tid == "scene" else f"{tid} ({self._label(ctx, tid)})" for tid in anim.get("targets") or []]
        text = f"After the child's last recording an animation played on: {', '.join(targets) or 'the picture'}."
        if kind != "uptake":
            return text  # the child noticed where it played, not what it was
        if short == "D4" and anim.get("word"):
            text += f" The word in the star was \"{anim['word']}\"."
        does = UPTAKE.get(short) or UPTAKE.get(short[:2]) or UPTAKE.get(short[:1])
        if meaning and does:
            text += f" A child who takes this animation up {does}."
        return text

    def _plan_text(self, plan: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        """Describe in prose what the child does in this recording according to the plan."""
        own = [m["detail"] for m in ctx.get("last_mistakes") or [] if m.get("detail")]
        if plan["kind"] == "uptake":
            if plan["name"]:
                kind = f"The child reacts to the animation by giving the character a name: {plan['name']}."
            elif own and plan["fix"]:
                kind = ("The child looks again at what the animation played on. The child's previous recording had this "
                        f"mistake: {'; '.join(own)}. If the animation played on that character or thing, the child now says "
                        "that part right, in a new sentence built differently from the earlier one (never the same sentence "
                        "with one word changed). If it played on something else, the child does what is said under "
                        "ON SCREEN JUST NOW. The child says just that, in a few plain words, and adds no other detail.")
            else:
                kind = ("The child looks again at what the animation played on and does what is said under "
                        "ON SCREEN JUST NOW, in a plain sentence about the story, in the child's usual words: "
                        "just that, roughly, with no other detail added. The child does not invent a name for anyone.")
        else:
            kind = {
                "narrate": "First recording on this picture: the child says what stands out most to them."
                if ctx["first_in_scene"] else
                "The child goes on with the story and says something new about the picture.",
                "partial": "The child noticed where the animation played and says something about that character or thing, "
                           "without changing anything said before and without guessing what the animation was for.",
                "meta": "Instead of going on with the story, the child reacts to the effect on screen: the child "
                        f"{self.rng.choice(self.habits['meta'])}. Very few words, the child's own; no question about "
                        "the effect and no description of it.",
                "repeat": "The child says the previous sentence again and adds one detail to it."
                if plan["mode"] == "expand" else
                "The child says the previous sentence again, shorter: only its main part, in the same words."
                if plan["mode"] == "shorter" else
                "The child says again almost the same thing as in the previous recording.",
                "stuck": "The child does not know what to say.",
                "fragment": "The child says an ordinary sentence about the picture (the recogniser will lose most of it).",
            }[plan["kind"]]
        lines = [f"- Kind: {kind}",
                 f"- Length: about {plan['length']} words spoken by the child, in "
                 + ("two short sentences." if plan.get("two") else
                    "one sentence, its parts simply joined with 'and'." if plan["length"] >= 16 else "one sentence.")]
        if plan.get("inner"):
            lines.append("- This recording is not about what can be seen: it says what a character wants, thinks or feels, "
                         "what is probably about to happen, or what someone ought to do. It is the child's own guess and "
                         "need not be right. A purpose or a reason may be given ('to ...', 'so that', 'because').")
        elif plan["kind"] not in ("fragment", "stuck", "meta"):
            lines.append("- This recording gives a reason for what happens (one 'because' or 'so')." if plan["reason"]
                         else "- No reason given in this recording: no 'because', 'so', 'since' or 'while'.")
        if plan.get("short_form"):
            lines.append("- The child uses one short form in this recording, where it comes naturally "
                         "(such as he's, she's, it's, there's, they're, doesn't, can't, don't).")
        if plan["slips"]:
            lines.append("- Content mistake to include: the child " + "; ".join(SLIPS[s]["text"] for s in plan["slips"]) + ".")
        elif plan["kind"] in ("narrate", "partial", "uptake", "repeat"):
            lines.append("- No new content mistake in this recording.")
        if plan["grammar"]:
            form = self.rng.choice(TENSE_FORMS) if plan["grammar"] == "tense_shift" else GRAMMAR[plan["grammar"]]
            lines.append(f"- Grammar mistake to include, once: the child {form}. "
                         "The example shows the kind of mistake only; make it on this picture's words.")
        elif plan["kind"] not in ("fragment", "stuck"):
            lines.append("- The child's grammar is correct in this recording (informal speech is fine).")
        if plan["disfluency"]:
            lines.append("- Hesitation to include, once: the child "
                         + DISFLUENCY[plan["disfluency"]].format(restart=self.rng.choice(RESTARTS), filler=self.rng.choice(FILLERS)) + ".")
        else:
            lines.append("- No hesitation, no repeated word.")
        if plan["adult"]:
            small = ", ".join(f"'{w}'" for w in self.habits["adult_prompt"] + self.habits["adult_confirm"])
            if self.traits.get("adult_specific"):
                lines.append(f"- Adult: {ADULT[plan['adult']]} What the adult says belongs to this picture and this moment: "
                             "a question that names one character or thing of this picture, a doubt about what the child "
                             "just said or a repeat of it, a wrong guess the child then puts right, or a word about the key "
                             "or the next picture. Never a bare stock prompt on its own. "
                             f"Small words this adult also uses (one of them at most here): {small}.")
            else:
                lines.append(f"- Adult: {ADULT[plan['adult']]} Words this adult often uses (one or two of them at most here): {small}.")
        elif self.traits["p_adult"] > 0:
            lines.append("- Only the child is heard in this recording: no adult speech.")
        who = " (the child's or the adult's)" if plan["adult"] else ""
        if plan["asr"]["mishear"] == "neighbour":
            lines.append(f"- Recogniser: it mis-hears ONE word{who}. In \"transcript\", print in its place a MORE COMMON "
                         "everyday word, no longer than the real one, that sounds close to it (the way 'cap' gets printed "
                         "for 'cat', 'chip' for 'ship', 'working' for 'walking'). Recognisers fall back on frequent words: "
                         "never a rarer word, never a longer one, never one word split in two, never a funny or clever one. "
                         "The sentence then makes a little less sense, and nothing else changes. "
                         "\"child_said\" keeps what was really said. Report it in \"misheard\".")
        elif plan["asr"]["mishear"] == "garble":
            lines.append(f"- Recogniser: it loses a stretch of three to six words{who}, spoken unclearly. In \"transcript\", "
                         "print in their place a short ordinary-looking string of words that has little to do with the picture "
                         "(recognisers invent such strings from unclear speech); the rest is printed as said. "
                         "\"child_said\" keeps what was really said. Report it in \"misheard\".")
        else:
            lines.append("- Recogniser: it hears every word right.")
        return "\n".join(lines)

    def build_prompt(self, plan: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        """Build the full prompt asking the model to write the child's recording for the plan and context."""
        p = self.profile
        diagnosis = ("officially diagnosed dyslexia" if p["diagnosis"] == "dis"
                     else "dyslexia reported by the family, no formal diagnosis; oral language a little stronger than diagnosed children of the same age")
        parts = [
            "CHILD PROFILE",
            f"- {p['age']} years old, {diagnosis}.",
            f"- {p['note']}",
            f"- Vocabulary: {VOCABULARY[p['age']]}.",
            f"- What this child looks at first in a picture: {self.habits['focus'][0]}, then {self.habits['focus'][1]}.",
            f"- Talking about characters, this child {self.habits['pronouns']}.",
            f"- This child {self.habits['contractions']}.",
        ]
        if p.get("grammar"):
            parts.append("- When this child makes a grammar mistake it is always of the same kind: the child "
                         + "; or ".join(GRAMMAR[g] for g in p["grammar"]) + ". Any other grammar mistake is out of character.")
        parts += [f"- {rule}" for rule in p.get("style", [])]
        if self.traits["p_adult"] > 0:
            parts.append("- An adult sits next to the child and sometimes speaks.")
        examples = self._examples(ctx["story"])
        if examples:
            parts += ["", "REFERENCE TRANSCRIPTS (real recordings, similar profile, other pictures: register only)"]
            parts += [f"- {e}" for e in examples]
        if ctx.get("story_so_far"):
            parts += ["", "WHAT THIS CHILD SAID ABOUT THE EARLIER PICTURES OF THIS STORY"]
            parts += [f"- {u}" for u in ctx["story_so_far"][-6:]]
        if self.traits.get("picture_only") and ctx.get("image"):
            parts += ["", f"CURRENT PICTURE (number {ctx['scene_number']} of {ctx['scene_count']}; attached). "
                      "There is no written description: the child has only the drawing, and so have you. "
                      "Name things as this child would on seeing them, with the first everyday word that comes, even a rough "
                      "one. Whatever the drawing does not settle (which bird, what kind of water, ground or building, who "
                      "people are to each other, what just happened) the child settles in their own way, and keeps to it."]
        else:
            parts += ["", f"CURRENT PICTURE (number {ctx['scene_number']} of {ctx['scene_count']}; "
                      + ("attached; written description for checking only)" if ctx.get("image") else "description for you only)"),
                      ctx["scene_description"]]
        parts += ["In the picture, with this child's word for each: "
                  + "; ".join(f"{e} = '{self._label(ctx, e)}'" for e in ctx["entities_in_scene"]) + "."]
        if ctx.get("named"):
            parts.append("Names this child has given: " + "; ".join(ctx["named"]) + ".")
        if ctx.get("absent"):
            parts.append("Seen in earlier pictures but NOT in this one: " + "; ".join(self._label(ctx, e) for e in ctx["absent"]) + ".")
        if ctx.get("sticky"):
            parts.append("Wrong words this child has been using in this story (keep them unless the plan says otherwise): "
                         + "; ".join(ctx["sticky"]) + ".")
        if ctx["said_in_scene"]:
            parts += ["", "ALREADY SAID ABOUT THIS PICTURE"] + [f"{i + 1}. {u}" for i, u in enumerate(ctx["said_in_scene"])]
        parts += ["", "ON SCREEN JUST NOW"]
        anim = ctx.get("animation")
        if anim and plan["kind"] in ("uptake", "partial", "meta"):
            parts.append(self._animation_text(anim, ctx, plan["kind"]))
        elif anim:
            text = "An animation played on the picture; the child did not pay attention to it."
            seen = [self._label(ctx, tid) for tid in anim.get("targets") or [] if tid != "scene"]
            if seen and plan["kind"] == "narrate" and self.traits.get("ignore_target"):
                # A child who ignores the cue does not happen to answer it
                text += f" The child goes on about something else in the picture, not about {' or '.join(seen)}."
            parts.append(text)
        elif ctx["first_in_scene"]:
            parts.append("A new picture has just appeared.")
        else:
            parts.append("Nothing visible happened after the child's last recording.")
        parts += ["", "TURN PLAN", self._plan_text(plan, ctx)]
        return "\n".join(parts)

    # ----------------------------------------------------------------- speak

    def speak(self, plan: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        """One recording: {transcript (sent to the app), child_said, mistakes, misheard, asr}."""
        last = ctx.get("last_spoken")
        if plan["kind"] == "fragment" and plan["mode"] == "stray":
            return {"transcript": self.rng.choice(STRAY), "child_said": "", "mistakes": [], "misheard": [], "asr": ["stray"]}
        if plan["kind"] == "repeat" and plan["mode"] == "verbatim" and last:
            # The same recording arrives again, as when a child presses the key twice
            return {**last, "asr": ["duplicate"]}
        if plan["kind"] == "stuck" and not plan["adult"]:
            text = self.rng.choice(self.stuck)
            return {"transcript": text, "child_said": text, "mistakes": [], "misheard": [], "asr": []}
        spoken = self._write(plan, ctx)
        spoken["transcript"], spoken["asr"] = recognise(spoken["transcript"], plan["asr"], self.rng, plan.get("keep"),
                                                          self.habits.get("inserts"))
        if spoken["misheard"]:
            spoken["asr"].append("mishear")
        return spoken

    def _picture(self, path: str) -> bytes:
        """Bytes of a scene picture, cached per path."""
        if path not in self.pictures:
            self.pictures[path] = Path(path).read_bytes()
        return self.pictures[path]

    def _write(self, plan: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Ask the model to write the recording of the plan, retrying on failure, and return the result."""
        prompt, redone = self.build_prompt(plan, ctx), False
        contents: Any = prompt
        if ctx.get("image"):
            contents = [types.Part.from_bytes(data=self._picture(ctx["image"]), mime_type="image/png"), prompt]
        last: Optional[Exception] = None
        for attempt in range(6):  # a failed recording would abort the whole session
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        thinking_config=types.ThinkingConfig(thinking_budget=512),
                        response_mime_type="application/json",
                    ),
                )
                usage = response.usage_metadata
                if usage is not None:
                    self.usage["calls"] += 1
                    self.usage["tokens_in"] += usage.prompt_token_count or 0
                    self.usage["tokens_out"] += (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                data = json.loads(response.text)
                if isinstance(data, list) and data:  # the model sometimes wraps its answer in a list
                    data = data[0]
                transcript = " ".join(str(data.get("transcript", "")).split())
                if not transcript:
                    raise ValueError("empty transcript")
                child_said = data.get("child_said")
                echo = echoes(str(transcript if child_said is None else child_said), ctx.get("scene_description") or "")
                if (echo and self.traits.get("echo_guard") and not self.traits.get("picture_only")
                        and not redone and attempt < 5):
                    # One more try: the child must not speak in the words of the written description
                    redone = True
                    self.usage["redone"] = self.usage.get("redone", 0) + 1
                    note = ("\n\nREDO. Your last answer was: " + transcript + "\nIt reused words of the written description ("
                            + "; ".join(f"'{e}'" for e in echo) + "). Write the recording again, same plan, in this child's own "
                            "plainer words, without those describing words.")
                    contents = [contents[0], prompt + note] if isinstance(contents, list) else prompt + note
                    continue
                return {"transcript": transcript,
                        "child_said": " ".join(str(transcript if child_said is None else child_said).split()),
                        "mistakes": [m for m in data.get("mistakes") or [] if isinstance(m, dict) and m.get("type")],
                        "misheard": [m for m in data.get("misheard") or [] if isinstance(m, dict) and m.get("heard")]}
            except Exception as exc:  # network, quota, malformed JSON
                last = exc
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"child model failed after retries: {last}")
