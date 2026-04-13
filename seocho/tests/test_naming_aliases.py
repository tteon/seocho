"""Tests that naming aliases (Property/P, NodeDefinition/NodeDef,
RelationshipDefinition/RelDef) are fully interchangeable."""

import pytest


class TestPropertyAlias:
    def test_property_and_p_are_same_class(self):
        from seocho import P, Property

        assert P is Property

    def test_property_instance_matches_p_instance(self):
        from seocho import P, Property

        prop_a = Property(str, unique=True)
        prop_b = P(str, unique=True)
        assert type(prop_a) is type(prop_b)
        assert prop_a == prop_b

    def test_property_exported_from_top_level(self):
        import seocho

        assert hasattr(seocho, "Property")
        assert "Property" in seocho.__all__

    def test_property_usable_in_nodedef(self):
        from seocho import NodeDef, Property

        node = NodeDef(properties={"name": Property(str, unique=True)})
        assert "name" in node.properties
        assert node.properties["name"].unique is True


class TestNodeDefAlias:
    def test_nodedefinition_and_nodedef_are_same(self):
        from seocho import NodeDef, NodeDefinition

        assert NodeDefinition is NodeDef

    def test_nodedefinition_exported(self):
        import seocho

        assert hasattr(seocho, "NodeDefinition")
        assert "NodeDefinition" in seocho.__all__

    def test_nodedefinition_instance_works(self):
        from seocho import NodeDefinition, Property

        node = NodeDefinition(
            description="A person",
            properties={"name": Property(str, unique=True)},
        )
        assert node.description == "A person"


class TestRelDefAlias:
    def test_relationshipdefinition_and_reldef_are_same(self):
        from seocho import RelDef, RelationshipDefinition

        assert RelationshipDefinition is RelDef

    def test_relationshipdefinition_exported(self):
        import seocho

        assert hasattr(seocho, "RelationshipDefinition")
        assert "RelationshipDefinition" in seocho.__all__

    def test_relationshipdefinition_instance_works(self):
        from seocho import RelationshipDefinition

        rel = RelationshipDefinition(
            source="Person", target="Company", description="Employment"
        )
        assert rel.source == "Person"
        assert rel.cardinality == "MANY_TO_MANY"  # default


class TestBackwardCompat:
    """Ensure short aliases still work for existing code."""

    def test_p_still_works_in_full_ontology_definition(self):
        from seocho import NodeDef, Ontology, P, RelDef

        onto = Ontology(
            name="test",
            nodes={"Person": NodeDef(properties={"name": P(str, unique=True)})},
            relationships={"KNOWS": RelDef(source="Person", target="Person")},
        )
        assert "Person" in onto.nodes
        assert onto.nodes["Person"].properties["name"].unique is True

    def test_long_names_and_short_names_interoperate(self):
        from seocho import NodeDef, NodeDefinition, P, Property

        # Mix and match
        node_with_long = NodeDefinition(properties={"x": P(str)})
        node_with_short = NodeDef(properties={"x": Property(str)})

        assert type(node_with_long) is type(node_with_short)
