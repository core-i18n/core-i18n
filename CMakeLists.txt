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

# Build a QML plugin when Qt Qml is available.
option(CORE_I18N_BUILD_QML "Build the Qt QML plugin when Qt is available" ON)
if(CORE_I18N_BUILD_QML)
  find_package(QT NAMES Qt6 Qt5 COMPONENTS Core Qml QUIET)
  if(QT_FOUND)
    find_package(Qt${QT_VERSION_MAJOR} COMPONENTS Core Qml REQUIRED)
    set_target_properties(core-i18n PROPERTIES POSITION_INDEPENDENT_CODE ON)

    set(CORE_I18N_QML_DIR "${CMAKE_CURRENT_BINARY_DIR}/qml/CoreI18n")
    file(MAKE_DIRECTORY "${CORE_I18N_QML_DIR}")
    configure_file(qml/CoreI18n/qmldir "${CORE_I18N_QML_DIR}/qmldir" COPYONLY)

    add_library(core-i18n-qml MODULE src/core-i18n-qml.cpp)
    set_target_properties(core-i18n-qml PROPERTIES
      AUTOMOC ON
      PREFIX ""
      OUTPUT_NAME corei18nplugin
      LIBRARY_OUTPUT_DIRECTORY "${CORE_I18N_QML_DIR}")
    target_link_libraries(core-i18n-qml PRIVATE
      core-i18n
      Qt${QT_VERSION_MAJOR}::Core
      Qt${QT_VERSION_MAJOR}::Qml)

    install(TARGETS core-i18n-qml
      LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}/qt${QT_VERSION_MAJOR}/qml/CoreI18n"
      RUNTIME DESTINATION "${CMAKE_INSTALL_LIBDIR}/qt${QT_VERSION_MAJOR}/qml/CoreI18n")
    install(FILES qml/CoreI18n/qmldir
      DESTINATION "${CMAKE_INSTALL_LIBDIR}/qt${QT_VERSION_MAJOR}/qml/CoreI18n")
  else()
    message(STATUS "Qt Qml not found; the optional core-i18n QML plugin will not be built")
  endif()
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
