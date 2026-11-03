import os
import re

import yaml

import srim


def create_elementdb():
    dbpath = os.path.join(srim.__path__[0], "data", "elements.yaml")
    with open(dbpath) as f:
        return yaml.load(f, Loader=yaml.SafeLoader)


class ElementDB:
    """Element database at ``srim.data.elements.yaml``"""

    _db = create_elementdb()

    @classmethod
    def lookup(cls, identifier):
        """Looks up element from symbol, name, or atomic number

        Parameters
        ----------
        identifier : :obj:`str`, :obj:`int`
            Unique symbol, name, or atomic number of element

        Notes
        -----
            This class is used for creation of elements, ions,
            etc. but generally will not be needed by the user.
        """
        if isinstance(identifier, (bytes, str)):
            if re.match("^[A-Z][a-z]?$", identifier):  # Symbol
                return cls._lookup_symbol(identifier)
            elif re.match("^[A-Z][a-z]*$", identifier):  # Name
                return cls._lookup_name(identifier)
        elif isinstance(identifier, int):  # Atomic Number
            return cls._lookup_atomic_number(identifier)
        raise ValueError(
            f"identifier of type:{type(identifier)} value:{identifier} not value see doc"
        )

    @classmethod
    def _lookup_symbol(cls, symbol):
        """Looks up symbol in element database

        :param str symbol: Symbol of atomic element
        """
        return cls._db[symbol]

    @classmethod
    def _lookup_name(cls, name):
        """Looks element in database by name

        :param str name: (Full) Name of atomic element (British spelling)
        """
        for symbol in cls._db:
            if cls._db[symbol]["name"] == name:
                return cls._db[symbol]
        raise KeyError(f"name:{name} does not exist")

    @classmethod
    def _lookup_atomic_number(cls, atomic_number):
        """Look up element in database by atomic number (Z)

        :param int atomic_number: Atomic number of atomic element
        """
        for symbol in cls._db:
            if cls._db[symbol]["z"] == atomic_number:
                return cls._db[symbol]
        raise IndexError(f"atomic number:{atomic_number} does not exist")
