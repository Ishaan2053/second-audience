# Final Submission Checklist

Internal deadline: Nov 29, 2026, 6:00 PM IST. Hard deadline: 8:00 PM IST.

## Experiment

- [ ] `HYPOTHESIS.md` commit predates the experiment start time
- [ ] Lab runs stopped with enough buffer for events to reach results
- [ ] Final results JSON saved under `reports/`
- [ ] Screenshots of Flagsmith: flag, experiment setup, exposures, results
- [ ] `sa report` generated and reviewed by a human; every number traced to results or ledger
- [ ] Ledger and Flagsmith counts reconcile, or the difference is explained
- [ ] Lab and field traffic reported separately and labelled
- [ ] Ship or no-ship decision recorded in the audit log

## Repository

- [ ] Public, MIT licence, no secrets in history (secret scan clean)
- [ ] `main` green in CI
- [ ] `v1.0.0` tag and GitHub release with notes
- [ ] `/code-review` findings resolved or listed as known limitations
- [ ] `/ship` check run and its actual result recorded

## README

- [ ] One-line pitch and the corrected framing (two audiences of a product)
- [ ] Sections titled with the four judging criteria
- [ ] Track 01 section: hypothesis, flag, metric, result
- [ ] Track 02 section: operator commands, approval points, sample output
- [ ] Architecture diagram
- [ ] "Connect your own agent" instructions with the public URL
- [ ] Local setup, environment variable reference, test instructions
- [ ] Honest limitations: lab population, single provider, reference server, sample size
- [ ] Link to the demo video

## Demo video

- [ ] Problem, build, where Flagsmith fits, in that order
- [ ] Side-by-side agent behaviour
- [ ] Operator flow with an approval prompt on screen
- [ ] Flagsmith results page on screen
- [ ] Length within any limit stated on the submission form
- [ ] Uploaded and link tested in a private window

## Deployment

- [ ] Public endpoint responds at `/healthz` with the released commit hash
- [ ] An external MCP client completed a task end to end today

## Submission form

- [ ] Repo link, README, video link
- [ ] Track 01 and Track 02 both selected
- [ ] Submitted before 6:00 PM IST; confirmation saved
