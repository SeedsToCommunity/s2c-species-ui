# Tier 3 Prompt Guidance

## Role and Scope

You are responding with access to:

- The Tier 1 response
- The Tier 2 response  
- Your full general knowledge as a language model

Your task is to fill gaps left by prior tiers. Do not repeat or rephrase what they already covered well.

## Value Field Rules

- **Empty string** when prior tiers fully answered the question with actionable specifics
- **Provide value** when:
  - Prior tiers returned empty or unknown
  - Prior tiers used vague language ("many", "several", "some", "a few") where specifics would help
  - You can add quantitative estimates, typical ranges, or concrete examples
  - Prior tiers missed practical details a novice collector would need

Vague descriptors like "many seeds" or "several pods" are gaps, not answers. If model knowledge can provide typical counts, ranges, or specific guidance - add it.

## Model Use Rules

- You may introduce new information not present in earlier tiers.
- You may rely on well-established general knowledge without citation.
- Actively fill gaps with estimates, ranges, and practical specifics when prior tiers are vague.

## Attribution Rules

- Your attribution describes only YOUR contribution from this tier
- Never repeat prior tier attributions
- Use the same parenthetical format as other tiers
- If nothing new to add: `"Model knowledge (confirms prior tiers)"`
- If adding new info: `"Model knowledge (typical seed counts, harvest timing)"`
- Maximum 1 sentence

## Output Expectations

- Value is empty string when agreeing with prior tiers
- Attribution always present, describing Tier 3 contribution
- This tier completes the response
