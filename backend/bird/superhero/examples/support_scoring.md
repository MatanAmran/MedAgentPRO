
Answer:
1. [publisher] Sauron is published by J. R. R. Tolkien.
2. [publisher] Sauron first appeared in The Lord of the Rings.
Records:
[publisher] examined: superheroes with publishers where superhero name = 'Sauron'
[publisher] row 1: publisher.superhero_name = Sauron, publisher.publisher_name = J. R. R. Tolkien
Output: {"claims": [{"sentence": 1, "text": "Sauron is published by J. R. R. Tolkien.", "verdict": "supported", "evidence": ["publisher.superhero_name", "publisher.publisher_name"]}, {"sentence": 2, "text": "Sauron first appeared in The Lord of the Rings.", "verdict": "uncovered", "evidence": []}]}

Answer:
1. [flight] Abraxas is the only superhero with the power Flight.
2. [flight] Adam Strange also has the power Flight.
Records:
[flight] examined: superhero powers with superheroes with superpowers where power name = 'Flight'
[flight] flight.total = 208 rows matched, 30 shown
[flight] row 1: flight.superhero_name = Abraxas
[flight] row 2: flight.superhero_name = Adam Strange
Output: {"claims": [{"sentence": 1, "text": "Abraxas is the only superhero with the power Flight.", "verdict": "contradicted", "evidence": ["flight.total"]}, {"sentence": 2, "text": "Adam Strange also has the power Flight.", "verdict": "supported", "evidence": ["flight.superhero_name"]}]}

Answer:
1. [vampires] 2 superheroes have the race Vampire.
2. [vampires] Both are villains.
Records:
[vampires] examined: superheroes with races where race = 'Vampire' · computed the number of records
[vampires] vampires.total = 1 rows matched, 1 shown
[vampires] row 1: vampires.vampires = 2
Output: {"claims": [{"sentence": 1, "text": "2 superheroes have the race Vampire.", "verdict": "supported", "evidence": ["vampires.vampires"]}, {"sentence": 2, "text": "Both are villains.", "verdict": "uncovered", "evidence": []}]}
