"""MSO decision heads.

Choice/Noul use an ABSOLUTE per-option probability, not a softmax:

    mu_o = sigmoid(w . u_o)          computed independently per option
    P(o | O)  = mu_o
    P(none|O) = 1 - sum_{o in O} mu_o

GUARANTEE (conditional -- read this carefully):
    raw mu_o is ALWAYS independent of which other options are present.
    The reported probabilities inherit that independence *only while the
    validity constraint sum(mu) <= 1 holds*. When a miscalibrated model
    emits sum(mu) > 1 we must renormalise, and renormalising re-couples the
    options exactly the way softmax does. So sum(mu) <= 1 is not cosmetic:
    it is the condition under which option-set invariance and mass transfer
    are true at all. Hence:
      * score bias is initialised negative so mu starts small and valid,
      * training carries an explicit relu(sum(mu) - 1) penalty,
      * inference returns `valid`; the violation rate is a reported metric,
        not something to be silently patched away.
    tests/test_masstransfer.py checks both branches.

Score uses an ordinal cumulative-link head so level order holds by construction.
"""
from typing import NamedTuple
import torch
import torch.nn as nn
import torch.nn.functional as F

INIT_SCORE_BIAS = -3.0      # sigmoid(-3) ~= 0.047 -> sum(mu) valid for K up to ~20


class ChoiceOut(NamedTuple):
    probs: torch.Tensor         # [K] per-option probability
    abstain: torch.Tensor       # scalar P(none of the above)
    excess: torch.Tensor        # relu(sum(mu) - 1), the training penalty
    valid: bool                 # False => renormalised => invariance broken here


class OptionScorer(nn.Module):
    """u_o (option repr) + z_q (question repr) -> absolute probability mu_o."""

    def __init__(self, d_model: int, d_hidden: int = 1024, n_types: int = 3, norm: str = "sigmoid"):
        super().__init__()
        self.opt = nn.Sequential(nn.Linear(d_model, d_hidden), nn.GELU(), nn.Linear(d_hidden, d_hidden))
        self.que = nn.Sequential(nn.Linear(d_model, d_hidden), nn.GELU(), nn.Linear(d_hidden, d_hidden))
        self.score = nn.Linear(d_hidden, 1)
        nn.init.zeros_(self.score.weight)
        nn.init.constant_(self.score.bias, INIT_SCORE_BIAS)
        self.log_tau = nn.Parameter(torch.zeros(n_types))   # per question-type temperature
        self.norm = norm
        # v0.3: abstain logit from the gated question context; zero-init so a v0.2 head loads as-is
        self.abstain = nn.Linear(d_hidden, 1)
        nn.init.zeros_(self.abstain.weight)
        nn.init.zeros_(self.abstain.bias)
        # v0.4: the backbone's own opinion of each option (see mso/v04.py lm_option_feats) enters
        # both the gated representation and the logit directly; zero-init so older heads load as-is
        self.feat = nn.Linear(6, d_hidden)
        self.feat_lin = nn.Linear(6, 1)
        for m in (self.feat, self.feat_lin):
            nn.init.zeros_(m.weight)
            nn.init.zeros_(m.bias)

    def forward(self, u_opts: torch.Tensor, z_q: torch.Tensor, type_id: int = 0, feats=None) -> torch.Tensor:
        """u_opts [K, D], z_q [D] -> mu [K] in (0,1).
        sigmoid norm: each mu independent (sum <= 1 is a precondition).
        softmax norm (choice/score): softmax over the K option logits + one abstain logit, mu = the
        K option probabilities, so 1 - sum(mu) is exactly P(abstain). Noul stays sigmoid."""
        lg = self.logits(u_opts, z_q, type_id, feats)
        if self.norm == "softmax" and type_id != 0:
            return torch.softmax(lg, dim=0)[:-1]
        return torch.sigmoid(lg[:-1])

    def logits(self, u_opts: torch.Tensor, z_q: torch.Tensor, type_id: int = 0, feats=None) -> torch.Tensor:
        """The vector the probabilities come from, abstain last: [K option logits, abstain] for a
        softmax choice/score, [logit, 0] for noul and for the old sigmoid heads (softmax of
        [x, 0] is sigmoid(x), so forward() is unchanged). RLCD trains on this vector."""
        g = torch.tanh(self.que(z_q))
        h = self.opt(u_opts) * g[None, :]                               # FiLM-style gating
        if feats is not None:
            h = h + self.feat(feats)
        tau = torch.exp(self.log_tau[type_id])
        logit = self.score(h).squeeze(-1)
        if feats is not None:
            logit = logit + self.feat_lin(feats).squeeze(-1)
        logit = logit / tau
        if self.norm == "softmax" and type_id != 0:
            a = self.abstain(g).squeeze(-1) / tau
            return torch.cat([logit, a[None]])
        return torch.cat([logit, torch.zeros_like(logit[:1])])


def choice_outputs(mu: torch.Tensor, allow_abstain: bool = True) -> ChoiceOut:
    s = mu.sum()
    excess = torch.clamp(s - 1.0, min=0.0)
    valid = bool((s <= 1.0 + 1e-5).item())
    if not allow_abstain:
        return ChoiceOut(mu / s.clamp(min=1e-6), torch.zeros((), device=mu.device), excess, valid)
    if valid:
        return ChoiceOut(mu, (1.0 - s).clamp(min=0.0), excess, True)
    scaled = mu / s                                  # invariance is lost on this branch
    return ChoiceOut(scaled, torch.zeros((), device=mu.device), excess, False)


def jev_confidence(probs: torch.Tensor) -> torch.Tensor:
    """Jev's definition: (K*p_max - 1)/(K - 1). Uniform -> 0, one-hot -> 1."""
    k = probs.numel()
    if k < 2:
        return torch.ones((), device=probs.device)
    return ((k * probs.max() - 1.0) / (k - 1)).clamp(0.0, 1.0)


class OrdinalScoreHead(nn.Module):
    """Cumulative link: P(Y<=l) = sigmoid(theta_l - z), theta strictly increasing."""

    def __init__(self, d_model: int, d_hidden: int = 512):
        super().__init__()
        self.z = nn.Sequential(nn.Linear(d_model, d_hidden), nn.GELU(), nn.Linear(d_hidden, 1))
        self.cut = nn.Sequential(nn.Linear(d_model, d_hidden), nn.GELU(), nn.Linear(d_hidden, 1))

    def forward(self, z_q: torch.Tensor, u_levels: torch.Tensor) -> torch.Tensor:
        """z_q [D], u_levels [L, D] (levels in the caller's order) -> probs [L]."""
        z = self.z(z_q).squeeze(-1)
        raw = self.cut(u_levels).squeeze(-1)
        theta = raw[0] + torch.cat([torch.zeros(1, device=raw.device),
                                    torch.cumsum(F.softplus(raw[1:]), 0)])
        cdf = torch.sigmoid(theta - z)
        cdf = torch.cat([cdf[:-1], torch.ones(1, device=cdf.device)])
        return torch.cat([cdf[:1], cdf[1:] - cdf[:-1]]).clamp(min=1e-8)


# ---------------------------------------------------------------- losses ----
def log_score(p, target):
    """Proper. target is a distribution (soft labels welcome)."""
    return -(target * torch.log(p.clamp(min=1e-8))).sum()


def brier(p, target):
    """Proper, second order."""
    return ((p - target) ** 2).sum()


def rps(p, target):
    """Ranked probability score for ordered levels. Proper, order-aware."""
    return ((torch.cumsum(p, 0) - torch.cumsum(target, 0)) ** 2).sum()


def noul_loss(mu, target):
    """Binary log score on the absolute probability; target may be a frequency."""
    mu = mu.clamp(1e-6, 1 - 1e-6)
    return -(target * torch.log(mu) + (1 - target) * torch.log(1 - mu))


# focal loss and label smoothing are deliberately absent: neither is a proper
# scoring rule, both bias probabilities away from the true frequency.
