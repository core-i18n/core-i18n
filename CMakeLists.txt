cmake_minimum_required(VERSION 3.14)
project(core-i18n VERSION 0.1.0 LANGUAGES C CXX)

include(GNUInstallDirs)
find_package(Intl)
find_program(MSGFMT_EXECUTABLE msgfmt)

set(CORE_I18N_LOCALEDIR "${CMAKE_INSTALL_FULL_LOCALEDIR}" CACHE PATH "Where core-i18n.mo files are installed")

add_library(core-i18n src/core-i18n.c)
set_target_properties(core-i18n PROPERTIES VERSION ${PROJECT_VERSION} SOVERSION 0)
target_include_directories(core-i18n PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
target_compile_definitions(core-i18n PRIVATE CORE_I18N_LOCALEDIR="${CORE_I18N_LOCALEDIR}")
if(Intl_FOUND AND NOT Intl_IS_BUILT_IN)
  target_link_libraries(core-i18n PRIVATE Intl::Intl)
  set(CORE_I18N_PC_LIBS_PRIVATE "-lintl")
endif()

# Compile po/*.po into <locale>/LC_MESSAGES/core-i18n.mo
if(MSGFMT_EXECUTABLE)
  file(GLOB PO_FILES "${CMAKE_CURRENT_SOURCE_DIR}/po/*.po")
  set(MO_FILES)
  foreach(po ${PO_FILES})
    get_filename_component(lang ${po} NAME_WE)
    set(mo "${CMAKE_CURRENT_BINARY_DIR}/locale/${lang}/LC_MESSAGES/core-i18n.mo")
    add_custom_command(OUTPUT ${mo}
      COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_CURRENT_BINARY_DIR}/locale/${lang}/LC_MESSAGES"
      COMMAND ${MSGFMT_EXECUTABLE} -o ${mo} ${po}
      DEPENDS ${po})
    list(APPEND MO_FILES ${mo})
    install(FILES ${mo} DESTINATION "${CMAKE_INSTALL_LOCALEDIR}/${lang}/LC_MESSAGES")
  endforeach()
  add_custom_target(core-i18n-mo ALL DEPENDS ${MO_FILES})
else()
  message(WARNING "msgfmt not found; core-i18n.mo files will not be built")
endif()

install(TARGETS core-i18n EXPORT core-i18n-targets
  ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
  LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
  RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(FILES include/core-i18n.h DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

configure_file(cmake/core-i18n.pc.in core-i18n.pc @ONLY)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/core-i18n.pc DESTINATION ${CMAKE_INSTALL_LIBDIR}/pkgconfig)
