"""Title matching review: a dropped subtitle never hides a negation, a substituted content word
never matches, legitimate spelling/format variants still do."""

import pytest

from claimtrace.refaudit.match import title_match

ASPIRIN = "Aspirin {} mortality among adults with chronic cardiovascular disease"


@pytest.mark.parametrize(
    "cited, found",
    [
        # negation hidden in the omitted subtitle
        (
            "Aspirin reduces mortality in older adults",
            "Aspirin reduces mortality in older adults: no evidence from randomized trials",
        ),
        # direction / content substitutions above the similarity threshold
        (ASPIRIN.format("increases"), ASPIRIN.format("decreases")),
        (ASPIRIN.format("increases"), ASPIRIN.format("does not change")),
        (
            "Exercise improves outcomes in heart failure",
            "Exercise worsens outcomes in heart failure",
        ),
        ("Hypotension in elderly surgical patients", "Hypertension in elderly surgical patients"),
        (
            "Intracellular calcium signalling in neurons",
            "Intercellular calcium signalling in neurons",
        ),
        ("Infection rates after hip replacement", "Injection rates after hip replacement"),
        ("Drug X kinetics in vivo in rat livers", "Drug X kinetics in vitro in rat livers"),
        (
            "Gut microbiota of obese mice on a high-fat diet",
            "Gut microbiota of obese rats on a high-fat diet",
        ),
        (
            "Mortality before cardiac surgery in the elderly",
            "Mortality after cardiac surgery in the elderly",
        ),
        # an inserted content word
        (
            "Statins reduce mortality in older adults",
            "Statins reduce cardiac mortality in older adults",
        ),
    ],
)
def test_no_match(cited, found):
    assert title_match(cited, found) is False
    assert title_match(found, cited) is False


@pytest.mark.parametrize(
    "cited, found",
    [
        ("Tumour growth in paediatric patients", "Tumor growth in pediatric patients"),
        ("Analysing behaviour of colour models", "Analyzing behavior of color models"),
        (
            "Modelling the spread of influenza in schools",
            "Modeling the spread of influenza in schools",
        ),
        (
            "Dietary fibre intake and colorectal cancer risk",
            "Dietary fiber intake and colorectal cancer risk",
        ),
        (
            "Trauma centres and survival after severe injury",
            "Trauma centers and survival after severe injury",
        ),
        (
            "Host defence peptides against bacterial biofilms",
            "Host defense peptides against bacterial biofilms",
        ),
        (
            "Sulphur metabolism in marine bacterial communities",
            "Sulfur metabolism in marine bacterial communities",
        ),
        ("Non-linear dynamics of a data set", "Nonlinear dynamics of a dataset"),
        ("Genes & environment in childhood asthma", "Genes and environment in childhood asthma"),
        ("ATTENTION IS ALL YOU NEED", "attention is all you need"),
        ("Attention, is all -- you need!", "Attention is all you need."),
        ("Alzheimer's disease biomarkers in plasma", "Alzheimer disease biomarkers in plasma"),
        ("Viruses and diseases of the cell", "Virus and disease of the cells"),
        (
            "Prevalence studies of type II diabetes in rural India",
            "Prevalence study of type 2 diabetes in rural India",
        ),
        ("Vaccines don't cause autism in children", "Vaccines do not cause autism in children"),
        (
            "Why most published research findings are false",
            "Why most published research findings are false: an essay",
        ),
        (
            "RETRACTED: Ileal-lymphoid-nodular hyperplasia in children",
            "Ileal lymphoid nodular hyperplasia in children",
        ),
    ],
)
def test_match(cited, found):
    assert title_match(cited, found) is True
    assert title_match(found, cited) is True
