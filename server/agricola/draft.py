"""Pre-game pick-and-pass card draft.

Optional variant (host-configured at room creation): instead of keeping
the hand you were dealt, each player receives a PACKET of occupations,
keeps one card, and passes the rest along; repeat until everyone has
kept `keep` cards, then the same with minor improvements, passing the
other way. Passing is PIPELINED rather than lockstep: a packet moves on
the moment its holder picks, queueing up behind whatever its next
holder is still looking at. A player only ever sees the FRONT packet of
their queue (redact_view exposes exactly that), so pipelining leaks no
hidden information -- it just lets fast drafters keep reading while
slow ones think.

With `deal > keep` (e.g. deal 10, keep 7) each packet retires after
`keep` picks and its remaining cards are REMOVED from the game
(state["removed_cards"]) -- never into the draw piles, so draw effects
can't resurface cards the drafters have already seen. The undealt
remainder of the shuffled decks becomes the normal draw piles, exactly
as in a no-draft game.

Direction is a player-index delta: +1 passes to the next index ("left"
-- turn order proceeds clockwise, so the next player sits to your
left), -1 to the previous ("right").

Kept cards go straight into hand_occupations/hand_minors, so the
existing hand redaction, HandPanel rendering, and playability checks
all apply to the growing drafted hand unchanged.
"""

from server.agricola import cards

DEFAULT_DEAL = 7
DEFAULT_KEEP = 7

_DIRECTIONS = {
    "alternate": {"occupations": 1, "minors": -1},
    "left": {"occupations": 1, "minors": 1},
    "right": {"occupations": -1, "minors": -1},
}

_HAND_KEY = {"occupations": "hand_occupations", "minors": "hand_minors"}

_STAGE_LABEL = {"occupations": "occupations", "minors": "minor improvements"}


def resolve_options(options):
    """Parse a room's draft options into a config dict, or None when the
    room doesn't draft. `deal`/`keep` are clamped to sane bounds here;
    engine.initial_state additionally clamps them to what the selected
    decks can actually supply (deal_hands' auto-shrink)."""
    options = options or {}
    if options.get("draft_mode") != "pick_and_pass":
        return None

    def clamped(key, default, lo, hi):
        try:
            value = int(options.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    deal = clamped("draft_deal", DEFAULT_DEAL, 1, 14)
    keep = clamped("draft_keep", DEFAULT_KEEP, 1, deal)
    directions = options.get("draft_directions")
    if directions not in _DIRECTIONS:
        directions = "alternate"
    return {"deal": deal, "keep": keep,
            "directions": dict(_DIRECTIONS[directions])}


def start(state, config, occ_packets, minor_packets, log):
    """Install the draft onto a fresh initial state (hands empty,
    packets dealt by cards.deal_hands). Returns True if the draft
    finished immediately (a 1-card deal auto-resolves entirely)."""
    n = state["player_count"]
    state["phase"] = "draft"
    state["draft"] = {
        "stage": "occupations",
        "deal": config["deal"],
        "keep": config["keep"],
        "directions": config["directions"],
        "queues": [[packet] for packet in occ_packets],
        "picks_made": [0] * n,
        # Dealt up front (same shuffle as the occupations) but held back
        # until the occupations stage completes.
        "minor_packets": minor_packets,
    }
    log.append(f"— Draft: occupations "
               f"(passing {_passing(state['draft'])}) —")
    return _settle(state, log)


def apply_pick(state, pidx, cid, log):
    """Validate and apply one draft pick. Returns True when the whole
    draft just finished (the engine then deletes state["draft"] and
    starts round 1)."""
    draft = state.get("draft")
    if not draft:
        raise ValueError("No draft in progress")
    queue = draft["queues"][pidx]
    if not queue:
        raise ValueError("You have no packet to pick from")
    if cid not in queue[0]:
        raise ValueError("That card is not in your current packet")
    _take(state, draft, pidx, cid, log)
    return _settle(state, log)


def waiting_on(state):
    """Player indices who currently hold at least one packet."""
    draft = state.get("draft") or {}
    return [i for i, q in enumerate(draft.get("queues", [])) if q]


def valid_actions(state, pidx):
    draft = state["draft"]
    queue = draft["queues"][pidx]
    if not queue:
        return []
    label = _STAGE_LABEL[draft["stage"]]
    return [{
        "kind": "draft_pick",
        "card": cid,
        "description": f"Draft {cards.CARDS[cid]['name']} ({label})",
    } for cid in queue[0]]


def redact_view(view, pidx):
    """Replace the raw draft dict in a deep-copied view with what
    `pidx` may see (pidx None = spectator): their own FRONT packet in
    full, everything else as counts. Waiting packets stay hidden even
    from their holder -- you may only look at one packet at a time."""
    draft = view.get("draft")
    if not draft:
        return
    queues = draft.pop("queues")
    draft.pop("minor_packets", None)
    draft["your_packet"] = (list(queues[pidx][0])
                            if pidx is not None and queues[pidx] else None)
    draft["queue_counts"] = [len(q) for q in queues]


def phase_description(state):
    draft = state["draft"]
    return (f"Card draft — {_STAGE_LABEL[draft['stage']]}, "
            f"passing {_passing(draft)}")


# ── Internals ────────────────────────────────────────────────────────

def _passing(draft):
    return "left" if draft["directions"][draft["stage"]] == 1 else "right"


def _take(state, draft, pidx, cid, log, auto=False):
    """Move `cid` from the front packet into pidx's hand, then pass or
    retire the packet. Card names never reach the log -- picks are
    hidden information and the log is broadcast to everyone."""
    packet = draft["queues"][pidx].pop(0)
    packet.remove(cid)
    player = state["players"][pidx]
    player[_HAND_KEY[draft["stage"]]].append(cid)
    draft["picks_made"][pidx] += 1
    # A packet retires once `keep` picks have been taken from it; its
    # leftovers (deal - keep cards) leave the game entirely.
    taken = draft["deal"] - len(packet)
    if packet and taken < draft["keep"]:
        nxt = (pidx + draft["directions"][draft["stage"]]) \
            % state["player_count"]
        draft["queues"][nxt].append(packet)
    elif packet:
        state.setdefault("removed_cards", []).extend(packet)
    verb = "is dealt the last card of a packet" if auto else \
        (f"drafts a card ({_STAGE_LABEL[draft['stage']]} "
         f"{draft['picks_made'][pidx]}/{draft['keep']})")
    log.append(f"{player['name']} {verb}")


def _auto_picks(state, draft, log):
    """A 1-card packet is a forced pick (only possible when deal ==
    keep, on a packet's last visit) -- take it automatically so the
    final 'pick' of each packet costs nobody a click."""
    progressed = True
    while progressed:
        progressed = False
        for pidx in range(state["player_count"]):
            queue = draft["queues"][pidx]
            if queue and len(queue[0]) == 1:
                _take(state, draft, pidx, queue[0][0], log, auto=True)
                progressed = True


def _settle(state, log):
    """Run auto-picks and stage transitions until someone has a real
    choice to make. Returns True when the whole draft is complete
    (every queue empty <=> every player has made `keep` picks in the
    final stage)."""
    draft = state["draft"]
    while True:
        _auto_picks(state, draft, log)
        if any(draft["queues"]):
            return False
        if draft["stage"] == "occupations":
            draft["stage"] = "minors"
            draft["queues"] = [[packet]
                               for packet in draft.pop("minor_packets")]
            draft["picks_made"] = [0] * state["player_count"]
            log.append(f"— Draft: minor improvements "
                       f"(passing {_passing(draft)}) —")
            continue
        log.append("— Draft complete —")
        return True
