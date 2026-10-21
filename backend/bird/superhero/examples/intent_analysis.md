
Question: Who is the publisher of Sauron?
Output: {"disposition": "retrieve", "readings": [{"label": "publisher", "asked": "Who is the publisher of Sauron?", "plan": {"SELECT": [{"expr": {"col": "superhero.superhero_name"}, "as": "superhero_name"}, {"expr": {"col": "publisher.publisher_name"}, "as": "publisher_name"}], "FROM": "superhero", "JOIN": [{"rel": "superhero_publisher", "type": "INNER"}], "WHERE": {"EQ": [{"col": "superhero.superhero_name"}, {"value": "Sauron"}]}}}]}

Question: How many vampire superheroes are there?
Output: {"disposition": "retrieve", "readings": [{"label": "vampires", "asked": "How many vampire superheroes are there?", "plan": {"SELECT": [{"expr": {"func": "COUNT", "arg": "*"}, "as": "vampires"}], "FROM": "superhero", "JOIN": [{"rel": "superhero_race", "type": "INNER"}], "WHERE": {"EQ": [{"col": "race.race"}, {"value": "Vampire"}]}}}]}

Question: Calculate the average attribute value of all neutral superheroes.
Output: {"disposition": "retrieve", "readings": [{"label": "neutral_average", "asked": "Calculate the average attribute value of all neutral superheroes.", "plan": {"SELECT": [{"expr": {"func": "AVG", "arg": {"col": "hero_attribute.attribute_value"}}, "as": "avg_value"}], "FROM": "hero_attribute", "JOIN": [{"rel": "hero_attribute_hero", "type": "INNER"}, {"rel": "superhero_alignment", "type": "INNER"}], "WHERE": {"EQ": [{"col": "alignment.alignment"}, {"value": "Neutral"}]}}}]}

Question: Provide the hero name and race of Charles Chandler.
Output: {"disposition": "retrieve", "readings": [{"label": "hero_and_race", "asked": "Provide the hero name and race of Charles Chandler.", "plan": {"SELECT": [{"expr": {"col": "superhero.superhero_name"}, "as": "superhero_name"}, {"expr": {"col": "race.race"}, "as": "race"}], "FROM": "superhero", "JOIN": [{"rel": "superhero_race", "type": "INNER"}], "WHERE": {"EQ": [{"col": "superhero.full_name"}, {"value": "Charles Chandler"}]}}}]}

Question: What is the height of Sauron, and in which year did he first appear?
Output: {"disposition": "retrieve", "readings": [{"label": "height", "asked": "What is the height of Sauron", "plan": {"SELECT": [{"expr": {"col": "superhero.superhero_name"}, "as": "superhero_name"}, {"expr": {"col": "superhero.height_cm"}, "as": "height_cm"}], "FROM": "superhero", "WHERE": {"EQ": [{"col": "superhero.superhero_name"}, {"value": "Sauron"}]}}}], "unresolved": [{"asked": "in which year did he first appear", "needs": {"kind": "no_such_column", "entity": "superhero"}}]}

Question: Which publishers published more than 100 superheroes, and which of their superheroes are bad?
Output: {"disposition": "retrieve", "readings": [{"label": "large_publishers", "asked": "Which publishers published more than 100 superheroes", "plan": {"SELECT": [{"expr": {"col": "superhero.publisher_id"}, "as": "publisher_id"}, {"expr": {"col": "publisher.publisher_name"}, "as": "publisher_name"}, {"expr": {"func": "COUNT", "arg": "*"}, "as": "superheroes"}], "FROM": "superhero", "JOIN": [{"rel": "superhero_publisher", "type": "INNER"}], "GROUP_BY": ["publisher_id", "publisher_name"], "HAVING": [{"agg": "superheroes", "op": "GT", "value": 100}], "ORDER_BY": [{"by": "superheroes", "dir": "DESC"}]}}, {"label": "bad_superheroes", "asked": "which of their superheroes are bad", "plan": {"SELECT": [{"expr": {"col": "superhero.superhero_name"}, "as": "superhero_name"}, {"expr": {"col": "superhero.publisher_id"}, "as": "publisher_id"}], "FROM": "superhero", "JOIN": [{"rel": "superhero_alignment", "type": "INNER"}], "WHERE": {"AND": [{"EQ": [{"col": "alignment.alignment"}, {"value": "Bad"}]}, {"IN_READING": {"col": "superhero.publisher_id", "reading": "large_publishers", "as": "publisher_id"}}]}, "ORDER_BY": [{"by": "superhero_name", "dir": "ASC"}]}}]}

Question: Why are bad superheroes heavier than good ones?
Output: {"disposition": "refer_to_investigation"}

Question: Change the publisher of Sauron to Marvel Comics.
Output: {"disposition": "out_of_scope_action"}

Question: And for the other one?
Output: {"disposition": "needs_clarification"}
