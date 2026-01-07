# Tier 2 Prompt Guidance

## Role and Constraints

You are responding with access to:

- The Tier 1 response
- An additional set of secondary or more distant sources

Your task is to provide ONLY NEW information from these additional sources.

## Value Field Rules

- **If you agree with Tier 1 and have nothing new to add**: Set `"value": ""` (empty string)
- **If Tier 1 said "Unknown" and you can answer**: Provide the answer as value
- **If you have NEW information to add**: Include ONLY the new information in value
- **If you disagree with Tier 1**: Provide your different answer as value

The empty value signals "I agree with prior tier" - the UI will show Tier 1's value and your attribution as confirmation.

## Handling Conflicts

- If Tier 1 and Tier 2 sources differ or conflict, surface this explicitly in the value.
- Do not attempt to resolve conflicts unless a source explicitly does so.

## Attribution Rules

- Your attribution should ONLY list Tier 2 sources that contributed
- Never repeat Tier 1's attribution
- Use format: `Source (contribution summary)` with semicolons between sources
- For confirmations: `"Source (confirmed habitat types)"` or `"Source (confirmed timing)"`
- For new info: `"Source (adds prairie ecology); OtherSource (adds soil preferences)"`
- Example: `"Go Botany (meadows, fields); Illinois Wildflowers (oak savannas, prairies)"`

## Output Expectations

- Value is empty string when agreeing with Tier 1
- Attribution always present, describing Tier 2 source contributions
- This response may still be incomplete
