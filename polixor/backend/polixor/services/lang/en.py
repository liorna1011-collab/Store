"""חבילת השפה האנגלית."""

from __future__ import annotations

from .base import LanguagePack

ENGLISH = LanguagePack(
    code="en",
    name="English",
    direction="ltr",
    word_chars=r"\w'",
    script_ranges=((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F)),
    negations=frozenset({"not", "never", "don't", "didn't", "no", "wasn't", "isn't"}),

    stop_words=frozenset({
        "the", "a", "an", "and", "or", "but", "is", "was", "i", "you", "it",
        "that", "this", "to", "of", "in", "on", "so", "just", "really", "are",
        "be", "we", "they", "he", "she", "my", "your", "at", "for", "with",
        "as", "if", "not", "do", "did", "have", "has", "had",
    }),
    filler_tokens=frozenset({
        "um", "umm", "ummm", "uh", "uhh", "uhhh", "er", "erm", "ah", "ahh",
        "hmm", "hmmm", "mmm", "eh",
    }),
    filler_phrases=(
        "you know", "i mean", "sort of", "kind of", "like i said",
        "or something", "and stuff", "whatever",
    ),
    cta=(
        "follow me", "follow for", "subscribe", "hit the like", "link in bio",
        "comment below", "comments below", "let me know", "share this",
        "save this", "drop a comment", "check the link", "join",
    ),
    hook=(
        "listen", "stop scrolling", "nobody tells you", "here's the thing",
        "the biggest mistake", "i need to tell you", "what if i told you",
        "three ways", "the secret", "most people",
    ),
    topic_shift=(
        "so let's", "now let's", "moving on", "second thing", "next up",
        "but here's", "let's talk about", "another thing",
    ),
    emotion=(
        "i broke", "i cried", "i was afraid", "it hurt", "alone", "lost",
        "rock bottom", "couldn't believe", "insane", "shocked", "i won",
        "i made it", "i gave up", "i dreamed",
    ),
    claim=(
        "the reason", "because", "which means", "the truth is", "what happened",
        "the result", "that's why", "it works because",
    ),
    lexicon={
        "wow": 1.0, "oh my god": 1.0, "no way": 1.0, "insane": 0.9, "crazy": 0.85,
        "unbelievable": 0.95, "i can't believe": 0.95, "what the": 0.8, "holy": 0.85,
        "look at that": 0.9, "watch this": 0.95, "check this out": 0.9, "listen": 0.8,
        "let me tell you": 0.95, "first time": 0.9, "secret": 0.9, "actually": 0.5,
        "the truth is": 0.8, "story time": 0.95, "never told": 1.0,
        "clutch": 0.9, "we won": 0.9, "i won": 0.9, "we lost": 0.85, "i lost": 0.85,
        "world record": 0.95, "personal best": 0.85, "i did it": 0.9, "so close": 0.7,
        "funny": 0.75, "hilarious": 0.9, "lol": 0.6, "lmao": 0.7,
        "disagree": 0.8, "you're wrong": 0.85, "that's wrong": 0.75, "angry": 0.7,
    },
    category_patterns=(
        ("funny", ("funny", "hilarious", "lol", "lmao")),
        ("win", ("clutch", "we won", "i won", "i did it", "world record",
                 "personal best")),
        ("fail", ("we lost", "i lost", "fail", "failed")),
        ("surprise", ("wow", "no way", "unbelievable", "i can't believe")),
        ("story", ("let me tell you", "story time", "secret", "never told",
                   "first time")),
        ("argument", ("disagree", "you're wrong")),
        ("highlight", ("look at that", "watch this", "check this out")),
    ),
    emphasis_stop_words=frozenset({
        "the", "a", "an", "and", "or", "but", "is", "was", "i", "you", "it",
        "that", "this", "to", "of", "in", "on", "so", "just",
    }),
    number_words=(
        "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "twenty", "thirty", "hundred",
    ),
    list_promises=(
        "mistakes", "ways", "reasons", "rules", "steps", "tips", "things",
        "lessons", "principles", "questions",
    ),
    curiosity=("why", "how", "what happened", "who", "how many", "what if"),
    throat_clearing=(
        "so", "okay", "ok so", "alright", "hey guys", "hi everyone",
        "what's up", "let me start", "testing", "one two three",
        "so today", "basically",
    ),
    hanging_words=frozenset({
        "the", "a", "an", "of", "to", "in", "on", "at", "for", "with",
        "and", "or", "but", "is", "are", "was", "were", "that", "this",
        "my", "your", "his", "her", "its", "our", "their",
        "i", "we", "he", "she", "they", "you", "it",
    }),
    places=("house", "room", "office", "street", "road", "city", "beach",
            "mountain", "forest", "desert", "bridge", "station", "airport",
            "kitchen", "balcony", "shop", "cafe", "hotel"),
    objects=("computer", "laptop", "car", "phone", "camera", "book",
             "letter", "key", "door", "window", "desk", "bag", "bike",
             "motorcycle", "plane", "train", "money", "screen"),
    motions=("drove", "walked", "ran", "flew", "climbed", "went", "came",
             "arrived", "left", "opened", "closed", "built", "broke",
             "wrote", "filmed"),
    nature=("rain", "sun", "snow", "wind", "night", "morning", "evening",
            "cloud", "stars", "light", "darkness", "storm"),
    abstract=("i think", "i believe", "in my opinion", "important",
              "you should", "actually", "basically", "the question is",
              "the idea", "the problem is"),
    meta=("in this video", "as i said", "let me explain", "coming up",
          "watch this", "pay attention"),
    afk=("afk", "brb", "be right back", "right back", "give me a minute",
         "give me a sec", "gotta go to the bathroom", "one sec guys"),
    offtopic=("sponsor", "sponsored", "promo code", "discount code", "link in the description",
              "can you hear me", "can you see me", "stream crashed", "stream is down",
              "audio is broken", "lagging", "my mic", "getting water", "grab a drink",
              "thanks for the sub", "thanks for the follow", "thanks for the donation",
              "welcome everyone who just joined", "technical difficulties"),
)
