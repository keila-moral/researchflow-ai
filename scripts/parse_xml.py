import feedparser
import json
import sys

def parse_arxiv_xml(xml_content):
    feed = feedparser.parse(xml_content)
    results = []
    for entry in feed.entries:
        results.append({
            "id": entry.id,
            "title": entry.title.replace('\n', ' '),
            "summary": entry.summary.replace('\n', ' '),
            "published": entry.published,
            "link": entry.link
        })
    return results

if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r') as f:
            content = f.read()
            print(json.dumps(parse_arxiv_xml(content)))
