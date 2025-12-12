# Plant Species Directory - Spreadsheet Data Formats Guide

This document describes the data formats recognized by the Plant Species Directory app. When entering data into the Google Sheets (main species data or PlantData supplemental sheet), use these formats to enable automatic smart rendering.

---

## Text (Default)

Any plain text value that doesn't match the patterns below will be displayed as regular text.

**Example:**
```
This is a description of the plant.
```

---

## Single URL (Link)

A single URL starting with `http://` or `https://` will be displayed as a clickable button link.

**Example:**
```
https://www.prairiemoon.com/echinacea-pallida
```

**Displays as:** A styled button link

---

## Single Image URL

A URL ending in an image extension (`.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.svg`) will be displayed as an image.

**Example:**
```
https://example.com/photos/echinacea.jpg
```

**Displays as:** An embedded image

---

## JSON Dictionary of URLs (Named Links)

A JSON object where values are URLs. Each key becomes a button label.

**Format:**
```json
{"Label 1": "https://url1.com", "Label 2": "https://url2.com"}
```

**Example:**
```json
{"Prairie Moon": "https://prairiemoon.com/echinacea", "Illinois Wildflowers": "https://illinoiswildflowers.info/echinacea"}
```

**Displays as:** Multiple labeled button links

---

## JSON Dictionary of Image URLs (Image Gallery)

A JSON object where values are image URLs. Each key becomes an image caption.

**Format:**
```json
{"Caption 1": "https://example.com/img1.jpg", "Caption 2": "https://example.com/img2.jpg"}
```

**Example:**
```json
{"Flower closeup": "https://example.com/flower.jpg", "Full plant": "https://example.com/plant.jpg"}
```

**Displays as:** Multiple images with captions

---

## JSON Array of URLs (Link List)

A JSON array of URLs will be displayed as multiple button links.

**Format:**
```json
["https://url1.com", "https://url2.com", "https://url3.com"]
```

**Displays as:** Multiple button links (labeled as "Link 1", "Link 2", etc.)

---

## JSON Array of Image URLs (Image List)

A JSON array of image URLs will be displayed as multiple images.

**Format:**
```json
["https://example.com/img1.jpg", "https://example.com/img2.png", "https://example.com/img3.jpg"]
```

**Displays as:** Multiple images displayed together

---

## Chart Data (Monthly/Numeric Data)

A JSON object with numeric values will be displayed as a bar chart. Best for monthly observation counts or similar data.

**Format:**
```json
{"January": 0, "February": 0, "March": 2, "April": 15, "May": 42, "June": 38, ...}
```

**Requirements:**
- At least 80% of values must be numbers
- At least 2 key-value pairs

**Displays as:** A bar chart with labels on x-axis and values on y-axis

---

## Similar Species (Structured Comparison)

A special JSON format for describing similar species and how to distinguish them.

**Format:**
```json
{
  "topic": "similar_species",
  "similar_species": [
    {
      "name": "Species Name",
      "distinguishing_characteristics": "Description of how to tell them apart"
    }
  ]
}
```

**Example:**
```json
{
  "topic": "similar_species",
  "similar_species": [
    {
      "name": "Echinacea purpurea",
      "distinguishing_characteristics": "Has broader leaves and purple ray flowers that spread outward rather than drooping"
    },
    {
      "name": "Echinacea angustifolia",
      "distinguishing_characteristics": "Smaller overall size with narrower leaves"
    }
  ]
}
```

**Displays as:** A formatted section with "Similar Species" header and each species listed with its distinguishing characteristics

---

## HTML Content

Content containing HTML tags will be rendered as formatted HTML.

**Example:**
```html
<p>This plant has <strong>beautiful flowers</strong> and grows in <em>dry prairies</em>.</p>
```

**Displays as:** Formatted text with bold, italic, etc.

---

## Important Notes

1. **JSON must be valid** - Use a JSON validator if needed. Common issues:
   - Use double quotes `"` not single quotes `'`
   - No trailing commas after the last item
   - Escape special characters in strings

2. **Image URL detection** - URLs must end with a recognized image extension to be detected as images

3. **Google Drive images** - If using Google Drive image URLs, they may need to be converted to direct download format

4. **Mixed content** - If a JSON object has both image URLs and regular URLs, the majority type determines how it's displayed

---

## Quick Reference Table

| Data Type | Format | Example |
|-----------|--------|---------|
| Plain text | Any text | `Native prairie plant` |
| Single link | URL | `https://example.com` |
| Single image | Image URL | `https://example.com/photo.jpg` |
| Named links | JSON dict with URLs | `{"Site": "https://..."}` |
| Image gallery | JSON dict with image URLs | `{"Photo": "https://.../img.jpg"}` |
| Link list | JSON array of URLs | `["https://...", "https://..."]` |
| Image list | JSON array of image URLs | `["https://.../a.jpg", "https://.../b.jpg"]` |
| Chart | JSON dict with numbers | `{"Jan": 5, "Feb": 10, ...}` |
| Similar species | JSON with topic field | `{"topic": "similar_species", ...}` |
| HTML | HTML tags | `<p>Text with <b>bold</b></p>` |
