
Question: Who is the publisher of Sauron?
Records:
[publisher] examined: superheroes with publishers where superhero name = 'Sauron'
[publisher] publisher.total = 1 rows matched, 1 shown
[publisher] row 1: publisher.superhero_name = Sauron, publisher.publisher_name = J. R. R. Tolkien
Output: {"readings": [{"label": "publisher", "sentences": [{"text": "{publisher.superhero_name#1} is published by {publisher.publisher_name#1}."}]}]}

Question: How many vampire superheroes are there?
Records:
[vampires] examined: superheroes with races where race = 'Vampire' · computed the number of records
[vampires] vampires.total = 1 rows matched, 1 shown
[vampires] row 1: vampires.vampires = 2
Output: {"readings": [{"label": "vampires", "sentences": [{"text": "{vampires.vampires#1} superheroes have the race Vampire."}]}]}

Question: List the name of superheroes with flight power.
Records:
[flight] examined: superhero powers with superheroes with superpowers where power name = 'Flight'
[flight] flight.total = 208 rows matched, 30 shown
[flight] row 1: flight.superhero_name = Abraxas
[flight] row 2: flight.superhero_name = Adam Strange
[flight] row 3: flight.superhero_name = Air-Walker
Output: {"readings": [{"label": "flight", "sentences": [{"text": "{flight.total} superheroes have the power Flight, among them {flight.superhero_name#1}, {flight.superhero_name#2} and {flight.superhero_name#3}."}]}]}
