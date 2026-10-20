
Question: Who is the publisher of Sauron?
Output: {"disposition": "retrieve", "readings": [{"label": "publisher", "plan": {"SELECT": [{"expr": {"col": "superhero.superhero_name"}, "as": "superhero_name"}, {"expr": {"col": "publisher.publisher_name"}, "as": "publisher_name"}], "FROM": "superhero", "JOIN": [{"rel": "superhero_publisher", "type": "INNER"}], "WHERE": {"EQ": [{"col": "superhero.superhero_name"}, {"value": "Sauron"}]}}}]}

Question: How many vampire superheroes are there?
Output: {"disposition": "retrieve", "readings": [{"label": "vampires", "plan": {"SELECT": [{"expr": {"func": "COUNT", "arg": "*"}, "as": "vampires"}], "FROM": "superhero", "JOIN": [{"rel": "superhero_race", "type": "INNER"}], "WHERE": {"EQ": [{"col": "race.race"}, {"value": "Vampire"}]}}}]}

Question: Calculate the average attribute value of all neutral superheroes.
Output: {"disposition": "retrieve", "readings": [{"label": "neutral_average", "plan": {"SELECT": [{"expr": {"func": "AVG", "arg": {"col": "hero_attribute.attribute_value"}}, "as": "avg_value"}], "FROM": "hero_attribute", "JOIN": [{"rel": "hero_attribute_hero", "type": "INNER"}, {"rel": "superhero_alignment", "type": "INNER"}], "WHERE": {"EQ": [{"col": "alignment.alignment"}, {"value": "Neutral"}]}}}]}

Question: Provide the hero name and race of Charles Chandler.
Output: {"disposition": "retrieve", "readings": [{"label": "hero_and_race", "plan": {"SELECT": [{"expr": {"col": "superhero.superhero_name"}, "as": "superhero_name"}, {"expr": {"col": "race.race"}, "as": "race"}], "FROM": "superhero", "JOIN": [{"rel": "superhero_race", "type": "INNER"}], "WHERE": {"EQ": [{"col": "superhero.full_name"}, {"value": "Charles Chandler"}]}}}]}

Question: What is the height of Sauron, and in which year did he first appear?
Output: {"disposition": "retrieve", "readings": [{"label": "height", "plan": {"SELECT": [{"expr": {"col": "superhero.superhero_name"}, "as": "superhero_name"}, {"expr": {"col": "superhero.height_cm"}, "as": "height_cm"}], "FROM": "superhero", "WHERE": {"EQ": [{"col": "superhero.superhero_name"}, {"value": "Sauron"}]}}}], "unresolved": [{"asked": "in which year did he first appear", "needs": {"kind": "no_such_column", "entity": "superhero"}}]}

Question: Which publishers published more than 100 superheroes, and which of their superheroes are bad?
Output: {"disposition": "retrieve", "readings": [{"label": "large_publishers", "plan": {"SELECT": [{"expr": {"col": "publisher.publisher_name"}, "as": "publisher_name"}, {"expr": {"func": "COUNT", "arg": "*"}, "as": "superheroes"}], "FROM": "superhero", "JOIN": [{"rel": "superhero_publisher", "type": "INNER"}], "GROUP_BY": ["publisher_name"], "HAVING": [{"agg": "superheroes", "op": "GT", "value": 100}], "ORDER_BY": [{"by": "superheroes", "dir": "DESC"}]}}], "unresolved": [{"asked": "which of their superheroes are bad", "needs": {"kind": "cross_record_condition", "entity": "publisher"}}]}

Question: Why are bad superheroes heavier than good ones?
Output: {"disposition": "refer_to_investigation"}

Question: Change the publisher of Sauron to Marvel Comics.
Output: {"disposition": "out_of_scope_action"}

Question: And for the other one?
Output: {"disposition": "needs_clarification"}
