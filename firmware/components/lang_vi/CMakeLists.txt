idf_component_register(
    SRCS "src/normalize.c" "src/g2p.c" "src/lexicon.c"
    INCLUDE_DIRS "include"
    REQUIRES common
)
