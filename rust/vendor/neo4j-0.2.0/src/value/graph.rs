// Copyright Rouven Bauer
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use std::collections::HashMap;
use std::fmt::{Display, Formatter};
use thiserror::Error;

use super::ValueReceive;

#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct Node {
    pub id: i64,
    pub labels: Vec<String>,
    pub properties: HashMap<String, ValueReceive>,
    pub element_id: String,
}

impl Display for Node {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Node(labels={:?}, element_id={}, properties={:?})",
            self.labels, self.element_id, self.properties
        )
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Relationship {
    pub id: i64,
    pub start_node_id: i64,
    pub end_node_id: i64,
    pub type_: String,
    pub properties: HashMap<String, ValueReceive>,
    pub element_id: String,
    pub start_node_element_id: String,
    pub end_node_element_id: String,
}

impl Display for Relationship {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Relationship(type={}, element_id={}, start_node_element_id={}, end_node_element_id={}, properties={:?})",
            self.type_, self.element_id, self.start_node_element_id, self.end_node_element_id, self.properties
        )
    }
}

/// Represents a path in the graph.
///
/// It's not recommended to access the fields directly, but rather use [`Path::traverse()`] because
/// the fields' semantics are rather complicated and mutating them in a way that violates the
/// [invariants](`Path::verify_invariants()`) will cause many methods to panic.
#[derive(Debug, Clone, PartialEq)]
pub struct Path {
    pub nodes: Vec<Node>,
    pub relationships: Vec<UnboundRelationship>,
    /// See <https://neo4j.com/docs/bolt/current/bolt/structure-semantics/#structure-path> for
    /// details.
    pub indices: Vec<isize>,
}

impl Path {
    /// Initializes a new `Path` verifying its [invariants](`Path::verify_invariants()`).
    pub(crate) fn new(
        nodes: Vec<Node>,
        relationships: Vec<UnboundRelationship>,
        indices: Vec<isize>,
    ) -> Result<Self, PathInvariantError> {
        let path = Self::new_unchecked(nodes, relationships, indices);
        path.verify_invariants().map(|_| path)
    }

    /// Initializes a new `Path` without verifying its [invariants](`Path::verify_invariants()`)
    pub(crate) fn new_unchecked(
        nodes: Vec<Node>,
        relationships: Vec<UnboundRelationship>,
        indices: Vec<isize>,
    ) -> Self {
        Self {
            nodes,
            relationships,
            indices,
        }
    }

    /// Verifies the invariants of the path.
    ///
    /// # Invariants
    ///  * `indices`
    ///    * has an even number of elements
    ///    * odd entries (1st, 3rd, ...) are is in the range
    ///      `-self.relationships.len()..=-1` or `1..=self.relationships.len()`
    ///    * even entries (2nd, 4th, ...) are in the range `0..self.nodes.len()`
    ///  * `nodes` is not empty
    ///
    /// # Example
    /// ```
    /// # fn get_path() -> Path {
    /// #     doc_test_utils::with_transaction(|tx| {
    /// #         Ok(tx
    /// #             .query("CREATE p=(a:Person)-[:KNOWS]->(b:Person)-[:KNOWS]->(c:Person) RETURN p")
    /// #             .run()?
    /// #             .single()
    /// #             .unwrap()?
    /// #             .take_value("p")
    /// #             .unwrap()
    /// #             .try_into_path()
    /// #             .unwrap())
    /// #     })
    /// # }
    /// use neo4j::value::graph::Path;
    ///
    /// let mut path: Path = get_path();
    /// assert!(path.verify_invariants().is_ok());
    ///
    /// path.indices[0] = 0; // modification that violates the invariants
    /// assert!(path.verify_invariants().is_err());
    /// ```
    pub fn verify_invariants(&self) -> Result<(), PathInvariantError> {
        if self.nodes.is_empty() {
            return Err(PathInvariantError::EmptyNodes {});
        }
        if self.indices.len() % 2 != 0 {
            return Err(PathInvariantError::UnevenIndicesCount {});
        }
        let rel_len = self.relationships.len();
        if rel_len > (isize::MAX - 1) as usize {
            return Err(PathInvariantError::TooManyRelationships {});
        }
        let rel_len_i: isize = rel_len.try_into().expect("checked range above");
        let node_len = self.nodes.len();
        if node_len > isize::MAX as usize {
            return Err(PathInvariantError::TooManyNodes {});
        }
        let node_len_i: isize = node_len.try_into().expect("checked range above");
        for (i, idx) in self.indices.iter().enumerate() {
            if i % 2 == 0 {
                // relationship index
                if !(-rel_len_i..=-1).contains(idx) && !(1..=rel_len_i).contains(idx) {
                    return Err(PathInvariantError::EvenIndexOutOfRange {
                        index: i,
                        value: *idx,
                        relationships_len: rel_len,
                    });
                }
            } else {
                // node index
                if !(0..node_len_i).contains(idx) {
                    return Err(PathInvariantError::OddIndexOutOfRange {
                        index: i,
                        value: *idx,
                        nodes_len: node_len,
                    });
                }
            }
        }
        Ok(())
    }

    /// Returns the nodes and relationships in the order they appear on the path.
    ///
    /// The first element of the tuple is the start node of the path.
    /// The second element is a list of tuples, where each tuple contains a relationship and the
    /// next node on the path.
    ///
    /// # Panics
    /// Panics if `self.nodes`, `self.relationships` or `self.indices` has been tempered with, in
    /// a way that violates the [invariants of a `Path`](`Path::verify_invariants()`).
    /// Such an invalid path cannot be obtained from the database, as the database's return values
    /// are checked for validity before being converted to `Path`.
    pub fn traverse(
        &self,
    ) -> (
        &Node,
        Vec<(RelationshipDirection, &UnboundRelationship, &Node)>,
    ) {
        let mut result = Vec::with_capacity(self.indices.len() / 2);
        if self.indices.is_empty() {
            return (&self.nodes[0], result);
        }
        let mut index_iter = self.indices.iter();
        while let Some(mut relationship_idx) = index_iter.next().copied() {
            let next_node_idx: usize = index_iter
                .next()
                .expect("indices must contain an even number of elements")
                .to_owned()
                .try_into()
                .expect("2nd, 4th, ... entry in indices must be >= 0");
            let next_node = &self.nodes[next_node_idx];
            let direction = if relationship_idx < 0 {
                relationship_idx = -relationship_idx;
                RelationshipDirection::From
            } else {
                RelationshipDirection::To
            };
            relationship_idx -= 1;
            let relationship = {
                let relationship_idx: usize = relationship_idx
                    .try_into()
                    .expect("odd indices entries cannot be 0");
                &self.relationships[relationship_idx]
            };
            result.push((direction, relationship, next_node));
        }
        (&self.nodes[0], result)
    }
}

/// # Panics
/// Panics if [`Path`'s invariants](`Path::verify_invariants()`) are violated.
impl Display for Path {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        let (start_node, hops) = self.traverse();
        write!(f, "({})", start_node.element_id)?;
        for (direction, relationship, next_node) in hops {
            match direction {
                RelationshipDirection::To => {
                    write!(
                        f,
                        "-[{}]->({})",
                        relationship.element_id, next_node.element_id
                    )?;
                }
                RelationshipDirection::From => {
                    write!(
                        f,
                        "<-[{}]-({})",
                        relationship.element_id, next_node.element_id
                    )?;
                }
            }
        }
        Ok(())
    }
}

/// Represents an error that occurred because a [Path's invariants](`Path::verify_invariants()`)
/// were violated.
#[derive(Debug, Clone, Error, Eq, PartialEq)]
#[non_exhaustive]
pub enum PathInvariantError {
    /// [`Path::nodes`] must not be empty.
    #[error("nodes must not be empty")]
    #[non_exhaustive]
    EmptyNodes {},
    /// [`Path::indices`] must have an even number of elements.
    #[error("indices must have an even number of elements")]
    #[non_exhaustive]
    UnevenIndicesCount {},
    /// [`Path::relationships`] must be <= `[`isize::MAX`] - 1`.
    #[error("number of relationships must be <= {max} (isize::MAX - 1)", max = isize::MAX - 1)]
    #[non_exhaustive]
    TooManyRelationships {},
    /// [`Path::nodes`] must be <= [`isize::MAX`].
    #[error("number of nodes must be <= {max} (isize::MAX)", max = isize::MAX)]
    #[non_exhaustive]
    TooManyNodes {},
    /// [`Path::nodes`] must be <= [`isize::MAX`].
    #[error("indices[{index}]={value} must be in the range -{relationships_len}..=-1 or 1..={relationships_len}")]
    #[non_exhaustive]
    EvenIndexOutOfRange {
        index: usize,
        value: isize,
        relationships_len: usize,
    },
    /// [`Path::nodes`] must be <= [`isize::MAX`].
    #[error("indices[{index}]={value} must be in the range 0..{nodes_len}")]
    #[non_exhaustive]
    OddIndexOutOfRange {
        index: usize,
        value: isize,
        nodes_len: usize,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum RelationshipDirection {
    To,
    From,
}

impl Display for RelationshipDirection {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            RelationshipDirection::To => write!(f, "->"),
            RelationshipDirection::From => write!(f, "<-"),
        }
    }
}

/// Represents a relationship without its start and end nodes.
///
/// This type makes little sense on its own.
/// It's used in [`Path`] instead.
#[derive(Debug, Clone, PartialEq)]
pub struct UnboundRelationship {
    pub id: i64,
    pub type_: String,
    pub properties: HashMap<String, ValueReceive>,
    pub element_id: String,
}

#[cfg(test)]
mod test {
    use rstest::rstest;

    use super::*;

    fn get_nodes(n: usize, label: impl ToString, element_id: bool) -> Vec<Node> {
        let mut nodes = Vec::with_capacity(n);
        for i in 0..n {
            nodes.push(Node {
                id: i as i64,
                labels: vec![label.to_string()],
                properties: HashMap::new(),
                element_id: if element_id {
                    format!("n{}", i + 1)
                } else {
                    "".to_string()
                },
            });
        }
        nodes
    }

    fn get_rels(n: usize, type_: impl ToString, element_id: bool) -> Vec<UnboundRelationship> {
        let mut rels = Vec::with_capacity(n);
        for i in 0..n {
            rels.push(UnboundRelationship {
                id: n as i64,
                type_: type_.to_string(),
                properties: HashMap::new(),
                element_id: if element_id {
                    format!("r{}", i + 1)
                } else {
                    "".to_string()
                },
            })
        }
        rels
    }

    fn get_path() -> Path {
        // (n1)<-[r1]-(n2)-[r2]->(n1)-[r1]->(n3)
        let nodes = get_nodes(3, "Person", true);
        let relationships = get_rels(2, "KNOWS", true);
        let indices = vec![
            -1, // r1 (reverse)
            1,  // n2
            2,  // r2
            0,  // n1
            1,  // r1
            2,  // n3
        ];
        Path::new(nodes, relationships, indices).unwrap()
    }

    fn get_invalid_path_empty_nodes() -> Path {
        Path::new_unchecked(vec![], vec![], vec![])
    }

    fn get_invalid_path_uneven_indices_count() -> Path {
        let mut path = get_path();
        path.indices.pop();
        path
    }

    #[allow(dead_code)]
    fn get_invalid_path_too_many_relationships() -> Path {
        let mut path = get_path();
        path.relationships = get_rels((isize::MAX - 1) as usize, "", false);
        path
    }

    #[allow(dead_code)]
    fn get_invalid_path_too_many_nodes() -> Path {
        let mut path = get_path();
        path.nodes = get_nodes(isize::MAX as usize, "", false);
        path
    }

    fn get_invalid_path_even_index_out_of_range_1() -> Path {
        let mut path = get_path();
        path.indices[0] = 0;
        path
    }

    fn get_invalid_path_even_index_out_of_range_2() -> Path {
        let mut path = get_path();
        let rel_len_i: isize = path.relationships.len().try_into().unwrap();
        path.indices[2] = rel_len_i + 1;
        path
    }

    fn get_invalid_path_even_index_out_of_range_3() -> Path {
        let mut path = get_path();
        let rel_len_i: isize = path.relationships.len().try_into().unwrap();
        path.indices[2] = -rel_len_i - 1;
        path
    }

    fn get_invalid_path_odd_index_out_of_range_1() -> Path {
        let mut path = get_path();
        path.indices[1] = -1;
        path
    }

    fn get_invalid_path_odd_index_out_of_range_2() -> Path {
        let mut path = get_path();
        path.indices[3] = path.nodes.len().try_into().unwrap();
        path
    }

    #[test]
    fn test_path_display() {
        let path = get_path();
        assert_eq!(path.to_string(), "(n1)<-[r1]-(n2)-[r2]->(n1)-[r1]->(n3)");
    }

    #[test]
    fn test_path_traverse() {
        let path = get_path();
        let expected_node_ids = ["n1", "n2", "n1", "n3"];
        let expected_directions = [
            RelationshipDirection::From,
            RelationshipDirection::To,
            RelationshipDirection::To,
        ];
        let expected_relationship_ids = ["r1", "r2", "r1"];

        let (start_node, hops) = path.traverse();
        assert_eq!(start_node.element_id, expected_node_ids[0]);
        for (i, (direction, relationship, next_node)) in hops.iter().enumerate() {
            assert_eq!(direction, &expected_directions[i]);
            assert_eq!(relationship.element_id, expected_relationship_ids[i]);
            assert_eq!(next_node.element_id, expected_node_ids[i + 1]);
        }
    }

    #[rstest]
    #[case(get_path(), Ok(()))]
    #[case(
        get_invalid_path_empty_nodes(),
        Err(PathInvariantError::EmptyNodes {})
    )]
    #[case(
        get_invalid_path_uneven_indices_count(),
        Err(PathInvariantError::UnevenIndicesCount {})
    )]
    // #[case(
    //     get_invalid_path_too_many_relationships(),
    //     Err(PathInvariantError::TooManyRelationships {})
    // )]
    // #[case(
    //     get_invalid_path_too_many_nodes(),
    //     Err(PathInvariantError::TooManyNodes {})
    // )]
    #[case(
        get_invalid_path_even_index_out_of_range_1(),
        Err(PathInvariantError::EvenIndexOutOfRange {
            index: 0,
            value: 0,
            relationships_len: 2
        })
    )]
    #[case(
        get_invalid_path_even_index_out_of_range_2(),
        Err(PathInvariantError::EvenIndexOutOfRange {
            index: 2,
            value: 3,
            relationships_len: 2
        })
    )]
    #[case(
        get_invalid_path_even_index_out_of_range_3(),
        Err(PathInvariantError::EvenIndexOutOfRange {
            index: 2,
            value: -3,
            relationships_len: 2
        })
    )]
    #[case(
        get_invalid_path_odd_index_out_of_range_1(),
        Err(PathInvariantError::OddIndexOutOfRange {
            index: 1,
            value: -1,
            nodes_len: 3
        })
    )]
    #[case(
        get_invalid_path_odd_index_out_of_range_2(),
        Err(PathInvariantError::OddIndexOutOfRange {
            index: 3,
            value: 3,
            nodes_len: 3
        })
    )]
    fn test_path_invariants(#[case] path: Path, #[case] expected: Result<(), PathInvariantError>) {
        assert_eq!(path.verify_invariants(), expected);

        let Path {
            nodes,
            relationships,
            indices,
        } = path.clone();
        assert_eq!(
            Path::new(nodes, relationships, indices),
            expected.map(|_| path)
        );
    }
}
