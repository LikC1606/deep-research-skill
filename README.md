# Deep Research Skill

Task-driven research for Codex and other Agent Skills compatible tools.

Instead of expanding the user's surface keywords, this Skill starts from the
required deliverable, identifies the evidence that could change the answer,
then runs a `Search -> Read -> Pivot` loop. Its final synthesis separates read
evidence, inference, conflicts, practical implications, and unresolved gaps.

For Kaggle and other competitions, it treats rankings as identity and outcome
anchors. The primary research targets are participant postmortems, discussions,
interviews, repositories, validation decisions, failed experiments, resource
tradeoffs, and transferable lessons.

## Install as an Agent Skill

```bash
npx skills add LikC1606/deep-research-skill --skill deep-research
```

## Install as a Codex Plugin Marketplace

```bash
codex plugin marketplace add LikC1606/deep-research-skill
```

Then open the Plugins Directory and install **Deep Research** from
**LikC1606 Skills**.

## Layout

- `skills/deep-research/`: standalone Agent Skill discovered by skills.sh.
- `plugins/deep-research/`: OpenAI plugin package containing the same Skill.
- `.agents/plugins/marketplace.json`: Codex repository marketplace catalog.

The two distributed Skill copies are kept byte-identical.

## License

MIT
